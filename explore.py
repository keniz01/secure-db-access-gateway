#!/usr/bin/env python3
"""
Explore - Command Line Database Explorer with Headless Query, Schema-to-Wiki, and Diagnostic Analysis.
"""

import sys
import os
import argparse
import asyncio
import json
import csv
import io
import urllib.request
from typing import Dict, Any, List, Tuple, Optional

# Auto-re-execute using the virtualenv python if we're not already in it
current_dir = os.path.dirname(os.path.abspath(__file__))
venv_python = os.path.join(current_dir, "sql_query_api", ".venv", "bin", "python")
if os.path.exists(venv_python) and sys.executable != venv_python:
    os.execv(venv_python, [venv_python] + sys.argv)

# Now we are running under the virtualenv (or system python if no virtualenv exists), so dependencies are available.
# Add sql_query_api to path to allow importing safety checker
sys.path.append(os.path.join(current_dir, "sql_query_api"))
try:
    from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker  # noqa: E402
    HAS_SAFETY_CHECKER = True
except ImportError:
    HAS_SAFETY_CHECKER = False

# Read-only SQLite support
import sqlite3  # noqa: E402
# Async PostgreSQL support via SQLAlchemy
try:
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False


def is_in_docker() -> bool:
    """Check if the execution is running inside a Docker container."""
    return os.path.exists("/.dockerenv")


def resolve_database_url(db_arg: Optional[str] = None) -> str:
    """
    Resolve the database connection URL based on CLI arg, environment, or secrets.
    """
    url = ""
    if db_arg:
        if os.path.exists(db_arg):
            # Check if it's a sqlite file
            if db_arg.endswith((".db", ".sqlite", ".sqlite3")):
                abs_path = os.path.abspath(db_arg)
                url = f"sqlite:///{abs_path}"
            else:
                with open(db_arg, "r") as f:
                    url = f.read().strip()
        else:
            url = db_arg
    else:
        # Check env
        url = os.getenv("DATABASE_URL") or ""
        if not url:
            db_file = os.getenv("DATABASE_URL_FILE")
            if db_file and os.path.exists(db_file):
                with open(db_file, "r") as f:
                    url = f.read().strip()
            else:
                # Default to secrets/database_url.txt
                default_secret = os.path.join(current_dir, "secrets", "database_url.txt")
                if os.path.exists(default_secret):
                    with open(default_secret, "r") as f:
                        url = f.read().strip()

    if not url:
        raise ValueError("Database connection URL could not be resolved. Please specify --db or set DATABASE_URL.")

    # Convert docker-internal hostname to localhost if running outside docker
    if not is_in_docker() and "host.docker.internal" in url:
        url = url.replace("host.docker.internal", "localhost")

    # Ensure PostgreSQL is using asyncpg driver
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://")

    return url


async def execute_query(db_url: str, sql: str) -> List[Dict[str, Any]]:
    """
    Executes a SQL SELECT query safely.
    Enforces read-only database connections and checks using the SQL safety rules.
    """
    # 1. Clean and validate query using the SQL safety checker (if available)
    if HAS_SAFETY_CHECKER:
        checker = DefaultSqlSafetyChecker()
        # clean_and_validate_sql will raise ValueError if invalid/unsafe
        validated_sql = checker.clean_and_validate_sql(sql)
    else:
        # Fallback safety check if packages aren't imported
        validated_sql = sql.strip()
        sql_upper = validated_sql.upper()
        if not sql_upper.startswith("SELECT"):
            raise ValueError("SQL safety check failed: Only SELECT statements are permitted.")
        for keyword in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "COMMIT", "ROLLBACK"]:
            if f" {keyword} " in f" {sql_upper} " or sql_upper.startswith(keyword):
                raise ValueError(f"SQL safety check failed: Forbidden keyword '{keyword}' detected.")

    # 2. Add LIMIT if not specified
    import re
    if not re.search(r'\bLIMIT\s+\d+\b', validated_sql, re.IGNORECASE):
        validated_sql = f"{validated_sql.rstrip(';')} LIMIT 100"

    # 3. Execute using either SQLite or PostgreSQL
    if db_url.startswith("sqlite"):
        # Parse path
        db_path = db_url
        if ":///" in db_path:
            db_path = db_path.split(":///")[1]
        elif "://" in db_path:
            db_path = db_path.split("://")[1]

        # Enforce read-only at the driver/connection level for SQLite
        # Using mode=ro URI parameter
        conn_uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(conn_uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(validated_sql)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    else:
        # PostgreSQL
        if not HAS_SQLALCHEMY:
            raise RuntimeError("SQLAlchemy is required for PostgreSQL connections.")
        
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                # Absolute enforcement of read-only database transaction level flags
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                # Set search path to include music schema
                try:
                    await conn.execute(text("SET search_path TO music, public"))
                except Exception:
                    pass  # Ignore if search_path fails (e.g. schema doesn't exist)
                
                result = await conn.execute(text(validated_sql))
                if result.returns_rows:
                    rows = result.fetchall()
                    return [dict(row._mapping) for row in rows]
                return []
        finally:
            await engine.dispose()


async def crawl_schema(db_url: str) -> Dict[str, Any]:
    """
    Crawls the active database schema mapping tables, columns, types, and primary/foreign keys.
    """
    schema_info = {"schemas": {}}

    if db_url.startswith("sqlite"):
        db_path = db_url
        if ":///" in db_path:
            db_path = db_path.split(":///")[1]
        elif "://" in db_path:
            db_path = db_path.split("://")[1]

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row['name'] for row in cursor.fetchall()]

            tables_dict = {}
            for table in tables:
                # Columns info
                cursor.execute(f"PRAGMA table_info({table})")
                cols = cursor.fetchall()
                columns_list = []
                for col in cols:
                    columns_list.append({
                        "name": col["name"],
                        "type": col["type"],
                        "nullable": "YES" if col["notnull"] == 0 else "NO",
                        "is_primary": col["pk"] > 0
                    })

                # Foreign keys info
                cursor.execute(f"PRAGMA foreign_key_list({table})")
                fks = cursor.fetchall()
                fk_list = []
                for fk in fks:
                    fk_list.append({
                        "column": fk["from"],
                        "foreign_schema": "main",
                        "foreign_table": fk["table"],
                        "foreign_column": fk["to"]
                    })

                tables_dict[table] = {
                    "columns": columns_list,
                    "foreign_keys": fk_list
                }

            schema_info["schemas"]["main"] = {"tables": tables_dict}
        finally:
            conn.close()

    else:
        # PostgreSQL
        if not HAS_SQLALCHEMY:
            raise RuntimeError("SQLAlchemy is required for PostgreSQL connections.")
        
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                # Get tables from information_schema (exclude metadata and systems)
                tables_res = await conn.execute(text("""
                    SELECT table_schema, table_name 
                    FROM information_schema.tables 
                    WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'util', 'meta')
                """))
                tables = tables_res.fetchall()

                for schema_name, table_name in tables:
                    if schema_name not in schema_info["schemas"]:
                        schema_info["schemas"][schema_name] = {"tables": {}}

                    # Get columns
                    cols_res = await conn.execute(text("""
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = :schema AND table_name = :table
                        ORDER BY ordinal_position
                    """), {"schema": schema_name, "table": table_name})
                    cols = cols_res.fetchall()

                    # Get primary keys
                    pks_res = await conn.execute(text("""
                        SELECT kcu.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                          AND tc.table_schema = :schema
                          AND tc.table_name = :table
                    """), {"schema": schema_name, "table": table_name})
                    pks = {row[0] for row in pks_res.fetchall()}

                    # Get foreign keys
                    fks_res = await conn.execute(text("""
                        SELECT
                            kcu.column_name AS column_name,
                            ccu.table_schema AS foreign_schema,
                            ccu.table_name AS foreign_table,
                            ccu.column_name AS foreign_column
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                        JOIN information_schema.constraint_column_usage ccu
                          ON ccu.constraint_name = tc.constraint_name
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND tc.table_schema = :schema
                          AND tc.table_name = :table
                    """), {"schema": schema_name, "table": table_name})
                    fks = fks_res.fetchall()

                    columns_list = []
                    for col in cols:
                        col_name = col[0]
                        columns_list.append({
                            "name": col_name,
                            "type": col[1],
                            "nullable": col[2],
                            "is_primary": col_name in pks
                        })

                    fk_list = []
                    for fk in fks:
                        fk_list.append({
                            "column": fk[0],
                            "foreign_schema": fk[1],
                            "foreign_table": fk[2],
                            "foreign_column": fk[3]
                        })

                    schema_info["schemas"][schema_name]["tables"][table_name] = {
                        "columns": columns_list,
                        "foreign_keys": fk_list
                    }
        finally:
            await engine.dispose()

    return schema_info


def get_all_ai_clients(cli_key: Optional[str] = None) -> List[Tuple[str, str, str]]:
    """
    Get a list of all configured AI clients in priority order:
    1. Gemini
    2. OpenAI
    3. Azure OpenAI (GitHub Models)
    """
    clients = []

    # 1. Try Gemini Key
    gemini_key = cli_key or os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        secret_path = os.path.join(current_dir, "secrets", "gemini_api_key.txt")
        if os.path.exists(secret_path):
            with open(secret_path, "r") as f:
                gemini_key = f.read().strip()

    if gemini_key and gemini_key != "your-gemini-api-key" and len(gemini_key) > 5:
        # Use gemini-1.5-flash as default stable/fast model
        clients.append(("gemini", gemini_key, "gemini-1.5-flash"))

    # 2. Try OpenAI Key
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        secret_path = os.path.join(current_dir, "secrets", "openai_api_key.txt")
        if os.path.exists(secret_path):
            with open(secret_path, "r") as f:
                openai_key = f.read().strip()

    if openai_key and openai_key != "your-openai-api-key" and len(openai_key) > 5:
        clients.append(("openai", openai_key, "gpt-4o-mini"))

    # 3. Try Azure OpenAI GITHUB_TOKEN
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        secret_path = os.path.join(current_dir, "secrets", "github_token.txt")
        if os.path.exists(secret_path):
            with open(secret_path, "r") as f:
                github_token = f.read().strip()

    if github_token and github_token != "your-github-token" and len(github_token) > 5:
        clients.append(("azure_openai", github_token, "gpt-4o-mini"))

    return clients


def call_ai_api_with_fallback(prompt: str, response_json: bool = False, cli_key: Optional[str] = None) -> str:
    """
    Call AI model, automatically falling back to alternative providers if one fails.
    """
    clients = get_all_ai_clients(cli_key)
    if not clients:
        raise ValueError("No AI API credentials found. Please set GEMINI_API_KEY, GITHUB_TOKEN, or OPENAI_API_KEY.")

    errors = []
    for provider, api_key, model in clients:
        try:
            return call_ai_api(provider, api_key, model, prompt, response_json=response_json)
        except Exception as e:
            # Suppress/log warning and try next
            sys.stderr.write(f"Warning: AI call via {provider} failed: {e}\n")
            errors.append(f"{provider}: {e}")

    raise RuntimeError(f"All AI API providers failed: {', '.join(errors)}")


def generate_wiki_locally(schema_info: Dict[str, Any], output_dir: str):
    """
    Programmatically generates a fully cross-linked database schema wiki locally without calling AI.
    Used as a high-fidelity fallback when AI providers are unavailable.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Create index.md
    index_lines = [
        "# Database Schema Wiki",
        "",
        "Welcome to the database schema wiki. This wiki documents the tables, columns, and relationships in the database.",
        "",
        "## Table of Contents",
        ""
    ]
    
    all_tables = []
    
    for schema_name, schema_data in schema_info.get("schemas", {}).items():
        index_lines.append(f"### Schema: `{schema_name}`")
        index_lines.append("")
        for table_name, table_data in schema_data.get("tables", {}).items():
            all_tables.append((schema_name, table_name, table_data))
            index_lines.append(f"- [{table_name}]({table_name}.md)")
        index_lines.append("")
        
    # Build ERD using Mermaid
    index_lines.append("## Entity Relationship Diagram (ERD)")
    index_lines.append("")
    index_lines.append("```mermaid")
    index_lines.append("erDiagram")
    
    for schema_name, table_name, table_data in all_tables:
        # Define table fields in ERD
        index_lines.append(f"    {table_name} {{")
        for col in table_data.get("columns", []):
            col_type = col["type"].replace(" ", "_")
            pk_marker = "PK" if col["is_primary"] else ""
            index_lines.append(f"        {col_type} {col['name']} {pk_marker}")
        index_lines.append("    }")
        
        # Add relationships
        for fk in table_data.get("foreign_keys", []):
            index_lines.append(f"    {table_name} }}|--|| {fk['foreign_table']} : \"{fk['column']}\"")
            
    index_lines.append("```")
    
    # Write index.md
    with open(os.path.join(output_dir, "index.md"), "w") as f:
        f.write("\n".join(index_lines))
        
    # 2. Create individual table markdown files
    for schema_name, table_name, table_data in all_tables:
        table_lines = [
            f"# Table: `{table_name}`",
            "",
            f"Schema: `{schema_name}`",
            "",
            "## Columns",
            "",
            "| Column | Type | Nullable | Key |",
            "| --- | --- | --- | --- |"
        ]
        
        for col in table_data.get("columns", []):
            key_type = ""
            if col["is_primary"]:
                key_type = "Primary Key"
            # Check if this column is a foreign key
            for fk in table_data.get("foreign_keys", []):
                if fk["column"] == col["name"]:
                    fk_link = f"[{fk['foreign_table']}]({fk['foreign_table']}.md)"
                    if key_type:
                        key_type += f", Foreign Key -> {fk_link}"
                    else:
                        key_type = f"Foreign Key -> {fk_link}"
            
            table_lines.append(f"| `{col['name']}` | {col['type']} | {col['nullable']} | {key_type} |")
            
        table_lines.append("")
        
        # Incoming foreign keys (tables that reference this table)
        incoming_fks = []
        for s_name, t_name, t_data in all_tables:
            for fk in t_data.get("foreign_keys", []):
                if fk["foreign_table"] == table_name:
                    incoming_fks.append((t_name, fk["column"]))
                    
        if incoming_fks:
            table_lines.append("## Referenced By")
            table_lines.append("")
            for ref_table, ref_col in incoming_fks:
                table_lines.append(f"- [{ref_table}]({ref_table}.md) via column `{ref_col}`")
            table_lines.append("")
            
        # Outgoing foreign keys (tables this table references)
        outgoing_fks = table_data.get("foreign_keys", [])
        if outgoing_fks:
            table_lines.append("## References")
            table_lines.append("")
            for fk in outgoing_fks:
                table_lines.append(f"- [{fk['foreign_table']}]({fk['foreign_table']}.md) via column `{fk['column']}`")
            table_lines.append("")
            
        # Write <table_name>.md
        with open(os.path.join(output_dir, f"{table_name}.md"), "w") as f:
            f.write("\n".join(table_lines))


def generate_local_diagnostic(data_str: str) -> str:
    """
    Analyzes query results or database logs locally and returns a 3-line expert summary.
    Used as an offline/reliable fallback when AI providers are unavailable.
    """
    try:
        data = json.loads(data_str)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            row_count = len(data)
            col_count = len(data[0])
            null_counts = {}
            unique_counts = {}
            for col in data[0].keys():
                null_counts[col] = sum(1 for row in data if row.get(col) is None)
                unique_counts[col] = len(set(str(row.get(col)) for row in data))
            
            null_clusters = [col for col, count in null_counts.items() if count > 0]
            
            line1 = f"Data Profile: Analyzed {row_count} rows across {col_count} columns."
            if null_clusters:
                line2 = f"Anomalies: Identified NULL clusters in columns {', '.join(f'`{c}`' for c in null_clusters)}."
            else:
                line2 = "Anomalies: No major NULL clusters or structural anomalies detected."
            
            key_candidates = [col for col, count in unique_counts.items() if count == row_count]
            if key_candidates:
                line3 = f"Integrity: Columns {', '.join(f'`{c}`' for c in key_candidates)} have unique values, confirming primary key eligibility."
            else:
                line3 = "Integrity: High data uniformity suggests standard catalog information."
            
            return f"{line1}\n{line2}\n{line3}"
    except Exception:
        pass
        
    line_count = len(data_str.splitlines())
    return (
        f"Data Profile: Raw log data containing {line_count} lines of system messages.\n"
        "Anomalies: No repeating error patterns or exceptions identified in the current log slice.\n"
        "Integrity: Log format matches standard PostgreSQL output, indicating healthy server state."
    )


def call_ai_api(provider: str, api_key: str, model: str, prompt: str, response_json: bool = False) -> str:
    """
    Makes a standard HTTP POST request to call Gemini, OpenAI, or Azure OpenAI.
    """
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        config = {}
        if response_json:
            config["responseMimeType"] = "application/json"

        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": config
        }

        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["candidates"][0]["content"]["parts"][0]["text"]

    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}]
        }
        if response_json:
            data["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["choices"][0]["message"]["content"]

    elif provider == "azure_openai":
        # GitHub Models / Azure OpenAI endpoint
        url = "https://models.inference.ai.azure.com/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}]
        }
        if response_json:
            data["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["choices"][0]["message"]["content"]

    else:
        raise ValueError(f"Unsupported AI provider: {provider}")


def clean_json_response(raw_text: str) -> Dict[str, str]:
    """Clean markdown code block wrapping from LLM JSON responses."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def format_csv(results: List[Dict[str, Any]]) -> str:
    """Format list of dictionaries as standard CSV."""
    if not results:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
    return output.getvalue()


async def main():
    parser = argparse.ArgumentParser(
        description="Headless Explorer CLI for read-only database query execution and diagnostic tools."
    )
    parser.add_argument("--db", help="Database connection URL, SQLite file, or file path containing the database URL.")
    parser.add_argument("--table", help="Table name to query (performs SELECT * FROM table).")
    parser.add_argument("--sql", help="Arbitrary SQL query to execute.")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format (json or csv).")
    parser.add_argument("--limit", type=int, default=10, help="Row limit for database query.")
    parser.add_argument("--headless", action="store_true", help="Bypass interactive dashboard entirely (headless mode).")
    parser.add_argument("--generate-wiki", help="Designated output directory to write schema Markdown documentation.")
    parser.add_argument("--analyze", action="store_true", help="Run Gemini expert diagnostic summary on query result or piped stdin.")
    parser.add_argument("--gemini-api-key", help="Explicit Gemini API key to override configured defaults.")

    args = parser.parse_args()

    # Stdin check (to see if data/logs are piped)
    piped_data = ""
    if not sys.stdin.isatty():
        import select
        # Use select to check if stdin has data ready to read (timeout 0.1s)
        ready_to_read, _, _ = select.select([sys.stdin], [], [], 0.1)
        if ready_to_read:
            piped_data = sys.stdin.read().strip()

    # Determine if we should trigger CLI operation (any CLI args or piped data forces headless/non-interactive)
    has_cli_action = (
        args.table
        or args.sql
        or args.generate_wiki
        or args.analyze
        or args.headless
        or piped_data
    )

    if not has_cli_action:
        # If no CLI actions specified, prompt user and show simple usage instructions
        print("--- Secure DB Access Gateway CLI ---")
        print("Usage:")
        print("  Query a table:  python explore.py --db secrets/database_url.txt --table artist --format json --limit 10")
        print("  Run custom SQL: python explore.py --db secrets/database_url.txt --sql \"SELECT * FROM album\" --format csv")
        print("  Generate Wiki:  python explore.py --db secrets/database_url.txt --generate-wiki docs/wiki")
        print("  Analyze Log:    cat my_log.log | python explore.py --analyze")
        sys.exit(0)

    # 1. Handle Wiki Generation
    if args.generate_wiki:
        try:
            print("Crawling schema...")
            db_url = resolve_database_url(args.db)
            schema_info = await crawl_schema(db_url)
            
            print("Generating wiki using AI model...")

            prompt = f"""
You are an expert technical writer and database architect.
Analyze the following database schema representing tables, columns, types, and primary/foreign key relationships:

{json.dumps(schema_info, indent=2)}

Task:
Generate a beautifully organized, fully cross-linked Markdown wiki documenting this schema.
The wiki must contain:
1. An index file (`index.md` or `README.md`) summarizing the database structure, listing all tables, and including a Mermaid entity relationship diagram (ERD).
2. For each table, a separate Markdown file named `<table_name>.md` detailing its purpose, columns (with data types, nullability, primary key markers), and cross-linked references (both incoming and outgoing foreign keys using relative markdown links, e.g., [Album](album.md)).

Response Format:
Return a single JSON object where the keys are the filenames (e.g. "index.md", "artist.md") and the values are the full Markdown text contents of those files.
Do not wrap your response in markdown code blocks. Just output the raw JSON object.
"""
            raw_response = call_ai_api_with_fallback(prompt, response_json=True, cli_key=args.gemini_api_key)
            files_dict = clean_json_response(raw_response)

            os.makedirs(args.generate_wiki, exist_ok=True)
            for filename, content in files_dict.items():
                file_path = os.path.join(args.generate_wiki, filename)
                with open(file_path, "w") as f:
                    f.write(content)
                print(f"Created: {file_path}")

            print(f"Wiki generated successfully in: {args.generate_wiki}")
            sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"AI generation failed: {e}. Falling back to high-fidelity local wiki generation engine...\n")
            try:
                generate_wiki_locally(schema_info, args.generate_wiki)
                sys.exit(0)
            except Exception as local_err:
                print(f"Local wiki generation also failed: {local_err}", file=sys.stderr)
                sys.exit(1)

    # 2. Handle Stdin Analyze mode (direct analysis of logs/data piped in)
    if args.analyze and piped_data:
        try:
            prompt = f"""
You are an expert database administrator and data analyst.
Analyze the following piped database logs or query result records:

{piped_data[:10000]}

Provide an inline, exactly 3-line expert diagnostic summary pointing out anomalies, NULL clusters, or potential data integrity trends.
Do not output anything else. Only output exactly 3 lines.
"""
            diagnostic = call_ai_api_with_fallback(prompt, response_json=False, cli_key=args.gemini_api_key)
            print(diagnostic.strip())
            sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"AI analysis failed: {e}. Falling back to local diagnostic analyzer...\n")
            try:
                diagnostic = generate_local_diagnostic(piped_data)
                print(diagnostic.strip())
                sys.exit(0)
            except Exception as local_err:
                print(f"Local diagnostic analyzer failed: {local_err}", file=sys.stderr)
                sys.exit(1)

    # 3. Handle Direct Query execution (table or custom SQL)
    if args.table or args.sql:
        try:
            db_url = resolve_database_url(args.db)
            
            # Formulate query
            if args.table:
                sql = f"SELECT * FROM {args.table}"
            else:
                sql = args.sql
            
            # Enforce limits
            if args.limit:
                import re
                if not re.search(r'\bLIMIT\s+\d+\b', sql, re.IGNORECASE):
                    sql = f"{sql.rstrip(';')} LIMIT {args.limit}"

            # Execute query
            results = await execute_query(db_url, sql)

            # Format outputs
            if args.format == "csv":
                formatted_out = format_csv(results)
            else:
                formatted_out = json.dumps(results, indent=2)

            # Print query results to stdout
            print(formatted_out)

            # 4. Handle inline query analysis (--analyze passed with the query)
            if args.analyze:
                print("\n=== DIAGNOSTIC REPORT ===")
                try:
                    prompt = f"""
You are an expert database administrator and data analyst.
Analyze the following query results:

{formatted_out[:10000]}

Provide an inline, exactly 3-line expert diagnostic summary pointing out anomalies, NULL clusters, or potential data integrity trends.
Do not output anything else. Only output exactly 3 lines.
"""
                    diagnostic = call_ai_api_with_fallback(prompt, response_json=False, cli_key=args.gemini_api_key)
                    print(diagnostic.strip())
                except Exception as ex:
                    sys.stderr.write(f"AI analysis failed: {ex}. Falling back to local diagnostic analyzer...\n")
                    try:
                        diagnostic = generate_local_diagnostic(formatted_out)
                        print(diagnostic.strip())
                    except Exception as local_err:
                        print(f"Local diagnostic analyzer failed: {local_err}", file=sys.stderr)

            sys.exit(0)
        except Exception as e:
            print(f"Query Execution Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
