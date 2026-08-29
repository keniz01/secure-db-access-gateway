# Database Schema Wiki

Welcome to the database schema wiki. This wiki documents the tables, columns, and relationships in the database.

## Table of Contents

### Schema: `music`

- [artist](artist.md)
- [label](label.md)
- [genre](genre.md)
- [album](album.md)
- [record_label](record_label.md)
- [recording_artist](recording_artist.md)
- [track](track.md)

## Entity Relationship Diagram (ERD)

```mermaid
erDiagram
    artist {
        integer artist_id 
        character_varying artist_name 
    }
    label {
        integer label_id 
        character_varying label_name 
    }
    genre {
        integer genre_id PK
        character_varying genre_name 
    }
    album {
        integer album_id PK
        character_varying title 
        character_varying duration 
        integer total_tracks 
        integer release_year 
        integer genre_id 
        integer label_id 
        integer artist_id 
    }
    album }|--|| genre : "genre_id"
    album }|--|| record_label : "label_id"
    album }|--|| recording_artist : "artist_id"
    record_label {
        integer label_id PK
        character_varying label_name 
    }
    recording_artist {
        integer artist_id PK
        character_varying artist_name 
    }
    track {
        integer track_id PK
        character_varying title 
        character_varying duration 
        integer position 
        integer release_year 
        integer genre_id 
        integer label_id 
        integer artist_id 
        integer album_id 
    }
    track }|--|| genre : "genre_id"
    track }|--|| record_label : "label_id"
    track }|--|| recording_artist : "artist_id"
    track }|--|| album : "album_id"
```