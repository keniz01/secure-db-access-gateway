# Gateway policy package for the secure-db-access-gateway.
# This policy evaluates allow/deny decisions for database access requests.
#
# The OPA sidecar is expected to serve this policy at:
#   POST /v1/data/gateway/evaluate
#
# Input document structure:
# {
#   "principal": {"user_id": "...", "org_id": "...", "roles": ["viewer"]},
#   "database_id": "default",
#   "tables": ["album"],
#   "referenced_columns": ["album_id", "title"]
# }
#
# Output:
# {
#   "allowed": true/false,
#   "reason": "...",
#   "policy_ids": ["..."],
#   "row_restrictions": {"column": "subject_attribute"},
#   "masked_columns": ["column"]
# }

package gateway

import future.keywords.in

default allow = false

# Evaluate the request against all loaded policies.
evaluate := result {
    # Collect all applicable policies for this request
    applicable := [p | p := data.policies[_]; matches_policy(p, input)]

    # Check for deny policies first (deny precedence)
    denies := [p | p := applicable[_]; p.effect == "deny"]

    # If any deny policy matches, deny the request
    count(denies) > 0
    result := {
        "allowed": false,
        "reason": "Denied by policy.",
        "policy_ids": [p.id | p := denies[_]],
        "row_restrictions": {},
        "masked_columns": [],
    }
} else := result {
    # No deny policies; check for allow policies
    allows := [p | p := data.policies[_]; matches_policy(p, input); p.effect == "allow"; has_field(p, "table"); p.table != null]

    # All requested tables must be allowed
    allowed_tables := {p.table | p := allows[_]}
    all_tables_allowed := count({t | t := input.tables[_]; not t in allowed_tables}) == 0

    all_tables_allowed
    count(allows) > 0

    # Check column restrictions
    column_policies := [p | p := allows[_]; has_field(p, "columns"); count(p.columns) > 0]
    column_check_passed := check_columns(column_policies, input.referenced_columns)

    column_check_passed

    # Collect row restrictions and masked columns
    row_restrictions := collect_row_restrictions(allows, input.principal)
    masked := collect_masked_columns(allows)

    result := {
        "allowed": true,
        "reason": "Allowed by policy.",
        "policy_ids": [p.id | p := allows[_]],
        "row_restrictions": row_restrictions,
        "masked_columns": sort(to_array(masked)),
    }
} else := result {
    # No allow policies matched
    result := {
        "allowed": false,
        "reason": "No allow policy matched the requested table.",
        "policy_ids": [],
        "row_restrictions": {},
        "masked_columns": [],
    }
}

# effective_access computes the schema surface a principal may read.
effective_access := result {
    deny_all := has_deny_all(input.principal, input.database_id)
    deny_all
    result := {
        "accessible_tables": [],
        "allowed_columns": [],
        "masked_columns": [],
    }
} else := result {
    # Collect all allow policies for this principal/database
    # Use matches_policy_for_access which doesn't require input.tables
    allows := [p | p := data.policies[_]; matches_policy_for_access(p, input); p.effect == "allow"; has_field(p, "table"); p.table != null]

    # Check for deny policies on specific tables
    denies := [p | p := data.policies[_]; matches_policy_for_access(p, input); p.effect == "deny"]
    denied_tables := {p.table | p := denies[_]; has_field(p, "table"); p.table != null}

    accessible := {p.table | p := allows[_]; has_field(p, "table"); p.table != null; not p.table in denied_tables}

    # Collect column policies
    column_policies := [p | p := allows[_]; has_field(p, "columns"); count(p.columns) > 0]
    allowed_columns := {col | p := column_policies[_]; col := p.columns[_]}

    # Collect masked columns
    masked := {col | p := allows[_]; has_field(p, "masked_columns"); col := p.masked_columns[_]}

    result := {
        "accessible_tables": sort(to_array(accessible)),
        "allowed_columns": sort(to_array(allowed_columns)),
        "masked_columns": sort(to_array(masked)),
    }
}

# Helper: check if a field exists on an object
has_field(obj, field) {
    _ = obj[field]
}

# Helper functions

matches_policy(policy, input) {
    # Match on org_id (if specified in policy)
    has_field(policy, "org_id")
    policy.org_id != null
    policy.org_id == input.principal.org_id
    matches_policy_partial(policy, input)
} else {
    # org_id not specified - matches any org
    not has_field(policy, "org_id")
    matches_policy_partial(policy, input)
}

matches_policy_partial(policy, input) {
    # Match on principal_id (if specified)
    has_field(policy, "principal_id")
    policy.principal_id != null
    policy.principal_id == input.principal.user_id
    matches_policy_rest(policy, input)
} else {
    # principal_id not specified
    not has_field(policy, "principal_id")
    matches_policy_rest(policy, input)
}

matches_policy_rest(policy, input) {
    # Match on roles (if specified)
    has_field(policy, "roles")
    count(policy.roles) > 0
    role_matches(policy.roles, input.principal.roles)
    matches_policy_database(policy, input)
} else {
    # No role restriction
    not has_field(policy, "roles")
    matches_policy_database(policy, input)
}

matches_policy_database(policy, input) {
    # Match on database_id (if specified)
    has_field(policy, "database_id")
    policy.database_id != null
    policy.database_id == input.database_id
    matches_policy_table(policy, input)
} else {
    # database_id not specified
    not has_field(policy, "database_id")
    matches_policy_table(policy, input)
}

matches_policy_table(policy, input) {
    # Match on table (if specified)
    has_field(policy, "table")
    policy.table != null
    policy.table in {lower(t) | t := input.tables[_]}
} else {
    # table not specified (e.g., deny-all policy)
    not has_field(policy, "table")
}

# matches_policy_for_access is like matches_policy but doesn't require input.tables
# Used for effective_access where we enumerate all accessible tables
matches_policy_for_access(policy, input) {
    # Match on org_id (if specified in policy)
    has_field(policy, "org_id")
    policy.org_id != null
    policy.org_id == input.principal.org_id
    matches_policy_partial_access(policy, input)
} else {
    # org_id not specified - matches any org
    not has_field(policy, "org_id")
    matches_policy_partial_access(policy, input)
}

matches_policy_partial_access(policy, input) {
    # Match on principal_id (if specified)
    has_field(policy, "principal_id")
    policy.principal_id != null
    policy.principal_id == input.principal.user_id
    matches_policy_rest_access(policy, input)
} else {
    # principal_id not specified
    not has_field(policy, "principal_id")
    matches_policy_rest_access(policy, input)
}

matches_policy_rest_access(policy, input) {
    # Match on roles (if specified)
    has_field(policy, "roles")
    count(policy.roles) > 0
    role_matches(policy.roles, input.principal.roles)
    matches_policy_database_access(policy, input)
} else {
    # No role restriction
    not has_field(policy, "roles")
    matches_policy_database_access(policy, input)
}

matches_policy_database_access(policy, input) {
    # Match on database_id (if specified)
    has_field(policy, "database_id")
    policy.database_id != null
    policy.database_id == input.database_id
    # No table check needed for access enumeration
} else {
    # database_id not specified
    not has_field(policy, "database_id")
    # No table check needed for access enumeration
}

role_matches(policy_roles, principal_roles) {
    count({r | r := policy_roles[_]; r in principal_roles}) > 0
}

check_columns(column_policies, referenced_columns) {
    # If no columns are referenced or no column policies exist, pass
    count(referenced_columns) == 0
} else {
    count(column_policies) == 0
} else {
    # All referenced columns must be in the union of allowed columns
    allowed := {col | p := column_policies[_]; col := p.columns[_]}
    count({c | c := referenced_columns[_]; not c in allowed}) == 0
}

collect_row_restrictions(allows, principal) := restrictions {
    restrictions := {k: v |
        p := allows[_]
        has_field(p, "row_scope")
        some k, v in p.row_scope
    }
}

collect_masked_columns(allows) := masked {
    masked := {col | p := allows[_]; has_field(p, "masked_columns"); col := p.masked_columns[_]}
}

has_deny_all(principal, database_id) {
    p := data.policies[_]
    p.effect == "deny"
    not has_field(p, "table")
    matches_policy(p, {
        "principal": principal,
        "database_id": database_id,
        "tables": [],
    })
}

# Utility: convert set to array
to_array(set) := arr {
    arr := [x | x := set[_]]
}
