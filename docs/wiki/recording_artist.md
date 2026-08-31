# Table: `recording_artist`

Schema: `music`

## Columns

| Column | Type | Nullable | Key |
| --- | --- | --- | --- |
| `artist_id` | integer | NO | Primary Key |
| `artist_name` | character varying | YES |  |

## Referenced By

- [album](album.md) via column `artist_id`
- [track](track.md) via column `artist_id`
