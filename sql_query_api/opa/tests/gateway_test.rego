# Tests for the gateway authorization policy.
#
# The policy is the single authority for every data-access decision, and it
# previously had no tests at all: it did not compile against OPA 1.8.0 and
# nothing in CI noticed. The `opa-policy` job in .github/workflows/ci.yml runs
# `opa test` so a broken or regressed policy fails the build.
#
# Run against the opa/ directory, NOT policies/:
#   opa test sql_query_api/opa
# data.json lives one level above policies/, so testing policies/ alone loads
# no policy documents and every allow-case assertion fails.
package gateway

import data.gateway

# A viewer is allowed the album table data.json grants, and nothing else.
test_viewer_allowed_album if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["viewer"]},
		"database_id": "default",
		"tables": ["album"],
		"referenced_columns": ["album_id", "title"],
	}
	result.allowed == true
	result.policy_ids == ["allow-viewer-read-album"]
}

# Deny by default: a table with no matching policy must not be readable.
test_ungoverned_table_denied if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["viewer"]},
		"database_id": "default",
		"tables": ["secret_table"],
		"referenced_columns": ["x"],
	}
	result.allowed == false
}

# No roles means no access.
test_principal_without_roles_denied if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": []},
		"database_id": "default",
		"tables": ["album"],
		"referenced_columns": ["album_id"],
	}
	result.allowed == false
}

# An unknown role must not inherit anything.
test_unknown_role_denied if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["nonexistent"]},
		"database_id": "default",
		"tables": ["album"],
		"referenced_columns": ["album_id"],
	}
	result.allowed == false
}

# RBAC1: role_hierarchy grants admin the viewer surface.
test_admin_inherits_viewer if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["admin"]},
		"database_id": "default",
		"tables": ["album"],
		"referenced_columns": ["album_id"],
	}
	result.allowed == true
}

# Masked columns are readable but must be reported as masked.
test_masked_column_is_reported if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["editor"]},
		"database_id": "default",
		"tables": ["invoice"],
		"referenced_columns": ["invoice_id", "total"],
	}
	result.allowed == true
	result.masked_columns == ["total"]
}

# Column-level deny: a column outside the allow list fails the whole request.
test_column_outside_allow_list_denied if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["editor"]},
		"database_id": "default",
		"tables": ["invoice"],
		"referenced_columns": ["invoice_id", "secret_col"],
	}
	result.allowed == false
}

# A policy scoped to another database must not apply.
test_database_scoping if {
	result := gateway.evaluate with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["viewer"]},
		"database_id": "some_other_database",
		"tables": ["album"],
		"referenced_columns": ["album_id"],
	}
	result.allowed == false
}

# effective_access drives schema introspection, so it must also be deny-first.
test_effective_access_excludes_ungoverned_table if {
	result := gateway.effective_access with input as {
		"principal": {"user_id": "u1", "org_id": "org-a", "roles": ["viewer"]},
		"database_id": "default",
		"tables": [],
		"referenced_columns": [],
	}
	not "secret_table" in result.accessible_tables
	"album" in result.accessible_tables
}
