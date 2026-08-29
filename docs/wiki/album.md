# Table: `album`

Schema: `music`

## Columns

| Column | Type | Nullable | Key |
| --- | --- | --- | --- |
| `album_id` | integer | NO | Primary Key |
| `title` | character varying | YES |  |
| `duration` | character varying | YES |  |
| `total_tracks` | integer | YES |  |
| `release_year` | integer | YES |  |
| `genre_id` | integer | YES | Foreign Key -> [genre](genre.md) |
| `label_id` | integer | YES | Foreign Key -> [record_label](record_label.md) |
| `artist_id` | integer | YES | Foreign Key -> [recording_artist](recording_artist.md) |

## Referenced By

- [track](track.md) via column `album_id`

## References

- [genre](genre.md) via column `genre_id`
- [record_label](record_label.md) via column `label_id`
- [recording_artist](recording_artist.md) via column `artist_id`
