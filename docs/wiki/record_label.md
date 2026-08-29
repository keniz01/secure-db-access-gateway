# Table: `record_label`

Schema: `music`

## Columns

| Column | Type | Nullable | Key |
| --- | --- | --- | --- |
| `label_id` | integer | NO | Primary Key |
| `label_name` | character varying | YES |  |

## Referenced By

- [album](album.md) via column `label_id`
- [track](track.md) via column `label_id`
