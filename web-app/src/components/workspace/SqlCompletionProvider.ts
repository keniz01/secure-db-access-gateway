import type { languages, editor, Position as MonacoPosition } from 'monaco-editor';
import type { SchemaTable } from '../dashboard/SchemaBrowser';

/**
 * Builds a Monaco CompletionItemProvider from the cached schema tables.
 *
 * Handles:
 *  - `table.` / `alias.` → column completions
 *  - After FROM/JOIN → table name completions
 *  - Bare typing → table + keyword completions
 */
export const createSqlCompletionProvider = (
  tables: SchemaTable[]
): languages.CompletionItemProvider => {
  // Pre-build lookup maps
  const tableByName = new Map(tables.map((t) => [t.name.toLowerCase(), t]));

  // SQL keywords commonly used in SELECT queries (gateway is SELECT-only)
  const SQL_KEYWORDS = [
    'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'AS',
    'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON',
    'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET',
    'TABLE', 'VIEW', 'INDEX', 'PRIMARY', 'KEY', 'FOREIGN',
    'NULL', 'IS', 'LIKE', 'BETWEEN', 'EXISTS', 'DISTINCT',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
    'ASC', 'DESC', 'TRUE', 'FALSE',
  ];

  return {
    triggerCharacters: ['.', ' '],

    provideCompletionItems: (
      model: editor.ITextModel,
      position: MonacoPosition
    ): languages.CompletionList => {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endLineNumber: position.lineNumber,
        endColumn: word.endColumn,
      };

      const lineContent = model.getLineContent(position.lineNumber);
      const textBeforeCursor = lineContent.substring(0, position.column - 1);

      // --- CASE 1: table_alias. or table_name. → column completions ---
      const dotMatch = textBeforeCursor.match(
        /([a-zA-Z_][a-zA-Z0-9_]*)\.\s*$/i
      );
      if (dotMatch) {
        const qualifier = dotMatch[1].toLowerCase();

        // Try to find the table by alias or name
        const table =
          tableByName.get(qualifier) ??
          findTableByAlias(textBeforeCursor, tables);

        if (table) {
          const items: languages.CompletionItem[] = table.columns.map((col) => ({
            label: col.name,
            kind: 14, // Field
            detail: col.type,
            documentation: buildColumnDocumentation(col),
            insertText: col.name,
            range,
          }));

          return { suggestions: items };
        }
      }

      // --- CASE 2: after FROM/JOIN → table completions ---
      const fromMatch = textBeforeCursor.match(
        /\b(FROM|JOIN)\s+(\w*)$/i
      );
      if (fromMatch) {
        const items: languages.CompletionItem[] = tables.map((t) => ({
          label: t.name,
          kind: 14, // Field (table-like)
          detail: `${t.columns.length} columns`,
          documentation: `${t.schemaName}.${t.name}`,
          insertText: t.name,
          range,
        }));

        return { suggestions: items };
      }

      // --- CASE 3: bare typing → tables + keywords ---
      const items: languages.CompletionItem[] = [
        // Table completions
        ...tables.map((t) => ({
          label: t.name,
          kind: 14, // Field
          detail: 'table',
          documentation: `${t.schemaName}.${t.name} — ${t.columns.length} columns`,
          insertText: t.name,
          range,
        })),
        // Keyword completions
        ...SQL_KEYWORDS.filter((kw) =>
          kw.toLowerCase().startsWith(word.word.toLowerCase())
        ).map((kw) => ({
          label: kw,
          kind: 14, // Keyword
          detail: 'keyword',
          insertText: kw,
          range,
        })),
      ];

      return { suggestions: items };
    },
  };
};

/**
 * Heuristic: look for a table name earlier in the line that could be
 * the source of the alias. E.g. "FROM users u WHERE u." → "users" is
 * the source table for alias "u".
 */
function findTableByAlias(
  lineSoFar: string,
  tables: SchemaTable[]
): SchemaTable | null {
  const lower = lineSoFar.toLowerCase();

  // Check "FROM tableName alias" or "JOIN tableName alias" patterns
  const patterns = [
    /\bfrom\s+(\w+)\s+(\w+)\b/,
    /\bjoin\s+(\w+)\s+(\w+)\b/,
  ];

  for (const pattern of patterns) {
    const match = lower.match(pattern);
    if (match) {
      const tableName = match[1];
      const alias = match[2];

      // If the qualifier matches the alias, find the table by name
      const qualifier = lower.match(/([a-zA-Z_][a-zA-Z0-9_]*)\.\s*$/i)?.[1];
      if (qualifier === alias) {
        return tables.find((t) => t.name.toLowerCase() === tableName) ?? null;
      }
    }
  }

  return null;
}

function buildColumnDocumentation(col: {
  name: string;
  type: string;
  nullable: boolean;
  isPrimary: boolean;
}): string {
  const parts: string[] = [`**${col.name}** — \`${col.type}\``];
  if (col.isPrimary) parts.push('Primary key');
  if (col.nullable) parts.push('Nullable');
  return parts.join('\n');
}
