# Table: `genre`

Schema: `music`

## Columns

| Column | Type | Nullable | Key |
| --- | --- | --- | --- |
| `genre_id` | integer | NO | Primary Key |
| `genre_name` | character varying | NO |  |

## Referenced By

- [album](album.md) via column `genre_id`
- [track](track.md) via column `genre_id`
