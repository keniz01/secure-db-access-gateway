#!/usr/bin/env bash
#
# auto_create_project.sh
#
# Fully automated helper to create a repo-scoped Projects (beta / v2) board,
# add a "Status" single-select field (if missing), and bulk-add existing
# issues into the project setting their Status to Backlog/To do/Done
# according to safe rules:
#   - closed -> Done
#   - open + assignee -> To do
#   - open + no assignee -> Backlog
#
# Safety:
# - Default DRY_RUN=1 (no changes). Set DRY_RUN=0 to perform changes.
# - Script avoids passing GraphQL variables via the CLI (some environments
#   turn variables into null). Instead it inlines owner/repo and IDs into
#   GraphQL strings, and properly JSON-escapes embedded values.
#
# Prereqs:
# - gh CLI v2.0+ installed and authenticated: gh auth login
# - jq installed
# - An account with repo + project permissions on the repository.
#
# Usage:
#   # Dry run (safe)
#   bash .github/scripts/auto_create_project.sh
#
#   # To actually apply changes:
#   DRY_RUN=0 bash .github/scripts/auto_create_project.sh
#

set -euo pipefail

OWNER="keniz01"
REPO="secure-db-access-gateway"
PROJECT_NAME="Board - secure-db-access-gateway"
PROJECT_BODY="Kanban board for tracking issues (Backlog, To do, In progress, Review, Done)"
VISIBILITY="private"   # public | private
DRY_RUN="${DRY_RUN:-1}" # default 1 (dry run)

# helper: run a GraphQL query via gh and return JSON (no variables)
gh_graphql_inline() {
  local query="$1"
  # Using -f query= to pass the whole query; no variables used.
  gh api graphql -f query="$query" --silent
}

# helper: JSON-escape a string for safe embedding into GraphQL string literals
json_quote() {
  # outputs a quoted JSON string, e.g. "some\nvalue"
  jq -rn --arg s "$1" '$s|@json'
}

# sanity checks
if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI not found. Install from https://cli.github.com/ then run: gh auth login"
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq not found. Install jq to proceed."
  exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run: gh auth login"
  exit 2
fi

echo "Target repo: ${OWNER}/${REPO}"
echo "Project name: ${PROJECT_NAME}"
echo "Dry run: ${DRY_RUN}"
echo

read -p "Proceed with these settings? (y/N) " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Aborted by user."
  exit 0
fi

#
# 1) Create the project (if DRY_RUN=0) otherwise show what would be run.
#
echo
echo "1) Creating project (if it does not already exist)..."
create_cmd="gh project create --title \"$PROJECT_NAME\" --repo ${OWNER}/${REPO} --body \"$PROJECT_BODY\" --visibility ${VISIBILITY}"

# Fallback if gh rejects --repo: use --owner instead (uncomment to use)
# create_cmd="gh project create --title \"$PROJECT_NAME\" --owner \"$OWNER\" --body \"$PROJECT_BODY\" --visibility ${VISIBILITY}"

if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "-> Running: $create_cmd"
  eval "$create_cmd"
else
  echo "(DRY_RUN) Would run: $create_cmd"
fi

#
# 2) Locate the project node id by inlining owner & repo (avoid variables issues)
#
echo
echo "2) Locating the project node id..."
if [[ -z "$OWNER" || -z "$REPO" ]]; then
  echo "ERROR: OWNER or REPO empty"
  exit 2
fi

# Poll until the project shows up (max ~60s)
QL_QUERY=$(cat <<'GRAPHQL'
query {
  repository(owner:"__OWNER__", name:"__REPO__") {
    projectsV2(first: 50) {
      nodes {
        id
        title
        number
        url
      }
    }
  }
}
GRAPHQL
)
QL_QUERY="${QL_QUERY//__OWNER__/$OWNER}"
QL_QUERY="${QL_QUERY//__REPO__/$REPO}"

max_retries=12
retry=0
project_node=""
while [[ $retry -lt $max_retries ]]; do
  resp=$(gh api graphql -f query="$QL_QUERY" --silent) || resp=""
  project_node=$(echo "$resp" | jq -r '.data.repository.projectsV2.nodes[] | select(.title == "'"$PROJECT_NAME"'") | .id' 2>/dev/null || true)
  if [[ -n "$project_node" && "$project_node" != "null" ]]; then
    echo "Found project after $retry retries."
    break
  fi
  retry=$((retry+1))
  sleep 5
done

if [[ -z "$project_node" || "$project_node" == "null" ]]; then
  echo "ERROR: Could not find project '${PROJECT_NAME}' after polling. If gh rejected --repo, try replacing --repo with --owner in the create command."
  exit 2
fi

echo "Found project id: $project_node"
project_number=$(echo "$resp" | jq -r '.data.repository.projectsV2.nodes[] | select(.title == "'"$PROJECT_NAME"'") | .number' || true)
project_url=$(echo "$resp" | jq -r '.data.repository.projectsV2.nodes[] | select(.title == "'"$PROJECT_NAME"'") | .url' || true)

if [[ -z "$project_node" || "$project_node" == "null" ]]; then
  echo "ERROR: Could not find project '${PROJECT_NAME}' in the repository's Projects (beta)."
  echo "If you want the script to create the project now, re-run with DRY_RUN=0."
  exit 2
fi

echo "Found project:"
echo " - node id: $project_node"
echo " - number: $project_number"
echo " - url: $project_url"

#
# 3) Inspect project fields to find a 'Status' single-select field and its options
#
echo
echo "3) Inspecting project fields for a single-select 'Status' field..."
FIELDS_QUERY=$(cat <<'GRAPHQL'
query {
  node(id:"__PROJECT_ID__") {
    ... on ProjectV2 {
      id
      title
      fields(first: 50) {
        nodes {
          id
          name
          dataType
          ... on ProjectV2SingleSelectField {
            options {
              id
              name
            }
          }
        }
      }
    }
  }
}
GRAPHQL
)
FIELDS_QUERY="${FIELDS_QUERY//__PROJECT_ID__/$project_node}"
resp=$(gh_graphql_inline "$FIELDS_QUERY") || { echo "GraphQL call failed (fields)"; echo "$resp" | jq -C .; exit 2; }
echo "DEBUG: GraphQL response (fields):" >&2
echo "$resp" | jq -C . >&2 || true

status_field_id=$(echo "$resp" | jq -r '.data.node.fields.nodes[] | select(.name=="Status" and .dataType=="SINGLE_SELECT") | .id' || true)

if [[ -z "$status_field_id" || "$status_field_id" == "null" ]]; then
  echo "No single-select 'Status' field found for project."
  echo "You can create it manually in the UI, or allow the script to attempt to add it."
  read -p "Type 'create' to let the script attempt to create Status field now, or press Enter to abort: " step_choice
  if [[ "$step_choice" == "create" ]]; then
    # Build settings JSON for options and JSON-quote it for embedding in the GraphQL mutation
    settings_json=$(jq -n '{"options":[{"name":"Backlog"},{"name":"To do"},{"name":"In progress"},{"name":"Review"},{"name":"Done"}]}' | jq -R -s @json)
    # Create mutation with inlined projectId and quoted settings JSON
    CREATE_FIELD_MUT=$(cat <<'GRAPHQL'
mutation {
  addProjectV2Field(input: {projectId: "__PROJECT_ID__", name: "Status", settings: __SETTINGS__ }) {
    projectV2Field {
      id
    }
  }
}
GRAPHQL
)
    CREATE_FIELD_MUT="${CREATE_FIELD_MUT//__PROJECT_ID__/$project_node}"
    CREATE_FIELD_MUT="${CREATE_FIELD_MUT//__SETTINGS__/$settings_json}"

    echo
    if [[ "$DRY_RUN" -eq 0 ]]; then
      echo "Creating 'Status' field..."
      create_resp=$(gh_graphql_inline "$CREATE_FIELD_MUT") || { echo "GraphQL error creating field:"; echo "$create_resp" | jq -C .; exit 2; }
      status_field_id=$(echo "$create_resp" | jq -r '.data.addProjectV2Field.projectV2Field.id' || true)
      if [[ -z "$status_field_id" || "$status_field_id" == "null" ]]; then
        echo "Failed to create Status field. Response:"
        echo "$create_resp" | jq -C .
        exit 2
      fi
      echo "Created Status field id: $status_field_id"
    else
      echo "(DRY_RUN) Would create 'Status' field via GraphQL mutation (skipping actual mutation in dry run)."
      echo "Re-run with DRY_RUN=0 to create the field automatically."
      exit 0
    fi
  else
    echo "Aborting: please create the Status single-select field in the project UI and re-run."
    exit 2
  fi
else
  echo "Found Status field id: $status_field_id"
fi

#
# 4) Build a name -> option id map for Status options
#
echo
echo "4) Fetching Status field options..."
# Re-query fields to get options
resp=$(gh_graphql_inline "$FIELDS_QUERY") || { echo "GraphQL call failed (fields v2)"; echo "$resp" | jq -C .; exit 2; }
# Build map
declare -A OPTION_ID_BY_NAME
mapfile -t opt_lines < <(echo "$resp" | jq -r '.data.node.fields.nodes[] | select(.id=="'"$status_field_id"'") | .options[]? | "\(.name):::\(.id)"')
for line in "${opt_lines[@]:-}"; do
  name="${line%%:::*}"
  id="${line##*:::}"
  OPTION_ID_BY_NAME["$name"]="$id"
done

required_options=("Backlog" "To do" "In progress" "Review" "Done")
missing_opts=()
for opt in "${required_options[@]}"; do
  if [[ -z "${OPTION_ID_BY_NAME[$opt]:-}" ]]; then
    missing_opts+=("$opt")
  fi
done

if [[ ${#missing_opts[@]} -gt 0 ]]; then
  echo "Missing Status options: ${missing_opts[*]}"
  read -p "Type 'create' to add the missing options via GraphQL, or press Enter to abort: " add_choice
  if [[ "$add_choice" == "create" ]]; then
    for opt in "${missing_opts[@]}"; do
      # Create mutation that adds an option for the field; embed field id and option name
      # We need to JSON-quote the option name for safe embedding
      opt_quoted=$(json_quote "$opt")
      ADD_OPTION_MUT=$(cat <<'GRAPHQL'
mutation {
  addProjectV2FieldOption(input: {fieldId: "__FIELD_ID__", name: __OPT_NAME__}) {
    projectV2FieldOption {
      id
      name
    }
  }
}
GRAPHQL
)
      ADD_OPTION_MUT="${ADD_OPTION_MUT//__FIELD_ID__/$status_field_id}"
      # __OPT_NAME__ must be a JSON string (quoted)
      ADD_OPTION_MUT="${ADD_OPTION_MUT//__OPT_NAME__/$opt_quoted}"

      if [[ "$DRY_RUN" -eq 0 ]]; then
        addopt_resp=$(gh_graphql_inline "$ADD_OPTION_MUT") || { echo "GraphQL error adding option:"; echo "$addopt_resp" | jq -C .; exit 2; }
        new_id=$(echo "$addopt_resp" | jq -r '.data.addProjectV2FieldOption.projectV2FieldOption.id' || true)
        OPTION_ID_BY_NAME["$opt"]="$new_id"
        echo "Added option '$opt' -> id $new_id"
      else
        echo "(DRY_RUN) Would add option '$opt' to field $status_field_id"
      fi
    done
  else
    echo "Aborting: missing Status options. Please add them in the UI and re-run."
    exit 2
  fi
fi

echo
echo "Final Status options and ids:"
for opt in "${required_options[@]}"; do
  printf " - %s -> %s\n" "$opt" "${OPTION_ID_BY_NAME[$opt]}"
done

#
# 5) Gather issues and classify them
#
echo
echo "5) Gathering issues and classifying..."
issues_json=$(gh issue list --repo "${OWNER}/${REPO}" --state all --limit 1000 --json number,state,assignees 2>/dev/null)
mapfile -t issue_lines < <(echo "$issues_json" | jq -r '.[] | "\(.number) \(.state) \(.assignees|length)"')
declare -a backlog_list todo_list done_list
for line in "${issue_lines[@]:-}"; do
  num=$(awk '{print $1}' <<<"$line")
  state=$(awk '{print $2}' <<<"$line")
  assignees=$(awk '{print $3}' <<<"$line")
  if [[ "$state" == "closed" ]]; then
    done_list+=("$num")
  else
    if [[ "$assignees" -gt 0 ]]; then
      todo_list+=("$num")
    else
      backlog_list+=("$num")
    fi
  fi
done

echo "Counts:"
echo " - Backlog (open unassigned): ${#backlog_list[@]}"
echo " - To do (open assigned):    ${#todo_list[@]}"
echo " - Done (closed):            ${#done_list[@]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo
  echo "(DRY_RUN) Sample of items that would be added:"
  echo " Backlog: ${backlog_list[@]:0:20}"
  echo " To do:   ${todo_list[@]:0:20}"
  echo " Done:    ${done_list[@]:0:20}"
  echo
  echo "Re-run with DRY_RUN=0 to actually create project items and set Status values."
  exit 0
fi

#
# 6) Add items and set Status values
#
echo
echo "6) Adding items to project and setting Status..."
ADD_ITEM_MUT_TEMPLATE=$(cat <<'GRAPHQL'
mutation {
  addProjectV2Item(input: {projectId: "__PROJECT_ID__", contentId: "__CONTENT_ID__"}) {
    item {
      id
    }
  }
}
GRAPHQL
)

UPDATE_FIELD_MUT_TEMPLATE=$(cat <<'GRAPHQL'
mutation {
  updateProjectV2ItemFieldValue(input: {projectId: "__PROJECT_ID__", itemId: "__ITEM_ID__", fieldId: "__FIELD_ID__", value: __VALUE__}) {
    projectV2Item {
      id
    }
  }
}
GRAPHQL
)

# helper to add issue and set status
add_issue_to_project_with_status() {
  local issue_num="$1"
  local status_option_name="$2"
  echo "Processing issue #$issue_num -> $status_option_name"

  # Get issue node id (inline query)
  ISSUE_QUERY=$(cat <<'GRAPHQL'
query {
  repository(owner:"__OWNER__", name:"__REPO__") {
    issue(number: __NUMBER__) {
      id
      number
      url
    }
  }
}
GRAPHQL
)
  ISSUE_QUERY="${ISSUE_QUERY//__OWNER__/$OWNER}"
  ISSUE_QUERY="${ISSUE_QUERY//__REPO__/$REPO}"
  ISSUE_QUERY="${ISSUE_QUERY//__NUMBER__/$issue_num}"

  issue_resp=$(gh_graphql_inline "$ISSUE_QUERY") || { echo "  ERROR fetching issue #$issue_num"; echo "$issue_resp" | jq -C .; return 1; }
  issue_node_id=$(echo "$issue_resp" | jq -r '.data.repository.issue.id' || true)
  issue_url=$(echo "$issue_resp" | jq -r '.data.repository.issue.url' || true)
  if [[ -z "$issue_node_id" || "$issue_node_id" == "null" ]]; then
    echo "  ERROR: could not find issue #$issue_num (skipping)"
    return 1
  fi

  # Add item to project
  ADD_ITEM_MUT="${ADD_ITEM_MUT_TEMPLATE//__PROJECT_ID__/$project_node}"
  ADD_ITEM_MUT="${ADD_ITEM_MUT//__CONTENT_ID__/$issue_node_id}"
  add_resp=$(gh_graphql_inline "$ADD_ITEM_MUT") || { echo "  ERROR adding item: "; echo "$add_resp" | jq -C .; return 1; }
  item_id=$(echo "$add_resp" | jq -r '.data.addProjectV2Item.item.id' || true)
  if [[ -z "$item_id" || "$item_id" == "null" ]]; then
    echo "  ERROR: failed to add issue #$issue_num as a project item. Response:"
    echo "$add_resp" | jq -C .
    return 1
  fi
  echo "  Added as item id: $item_id -> $issue_url"

  # Set Status field value
  option_id="${OPTION_ID_BY_NAME[$status_option_name]}"
  if [[ -z "$option_id" ]]; then
    echo "  ERROR: missing option id for '$status_option_name'. Skipping status set."
    return 1
  fi

  # Build the value JSON and JSON-quote it so it can be embedded as __VALUE__ in the mutation
  value_json=$(jq -n --arg opt "$option_id" '{"singleSelectOptionId":$opt}')
  value_quoted=$(jq -R -s --arg v "$value_json" '$v|@json') # encloses value_json in quotes for GraphQL literal
  UPDATE_FIELD_MUT="${UPDATE_FIELD_MUT_TEMPLATE//__PROJECT_ID__/$project_node}"
  UPDATE_FIELD_MUT="${UPDATE_FIELD_MUT//__ITEM_ID__/$item_id}"
  UPDATE_FIELD_MUT="${UPDATE_FIELD_MUT//__FIELD_ID__/$status_field_id}"
  UPDATE_FIELD_MUT="${UPDATE_FIELD_MUT//__VALUE__/$value_quoted}"

  upd_resp=$(gh_graphql_inline "$UPDATE_FIELD_MUT") || { echo "  ERROR updating field: "; echo "$upd_resp" | jq -C .; return 1; }
  new_item_id=$(echo "$upd_resp" | jq -r '.data.updateProjectV2ItemFieldValue.projectV2Item.id' || true)
  if [[ -z "$new_item_id" || "$new_item_id" == "null" ]]; then
    echo "  ERROR: failed to set Status for item $item_id. Response:"
    echo "$upd_resp" | jq -C .
    return 1
  fi
  echo "  Status set to '$status_option_name' (option id: $option_id)"
  return 0
}

# Run for lists
for num in "${backlog_list[@]:-}"; do
  add_issue_to_project_with_status "$num" "Backlog" || echo "  (warning: issue $num failed)"
done
for num in "${todo_list[@]:-}"; do
  add_issue_to_project_with_status "$num" "To do" || echo "  (warning: issue $num failed)"
done
for num in "${done_list[@]:-}"; do
  add_issue_to_project_with_status "$num" "Done" || echo "  (warning: issue $num failed)"
done

echo
echo "Done. Visit the project at: $project_url"
echo "If any GraphQL errors occurred they were printed above."