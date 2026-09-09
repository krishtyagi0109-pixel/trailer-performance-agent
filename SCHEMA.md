Single flat table matching the columns inserted via client.insert_df('movie_trailers', df1[...]).

Table: movie_trailers
Column	Type	Nullable	Description
name	String / VARCHAR(255)	NO	Movie title
video_id	String / VARCHAR(50)	NO	Trailer video ID (e.g. YouTube video ID)
rating	String / VARCHAR(10)	YES	Content rating (e.g. "PG-13", "R")
genre	String / VARCHAR(100)	YES	Genre (e.g. "Action", "Drama")
year	Int16 / SMALLINT	YES	Release year
released	Date / VARCHAR(100)	YES	Full release date / string (e.g. "June 13, 1980 (United States)")
votes	Int64 / BIGINT	YES	Number of user votes/ratings
director	String / VARCHAR(255)	YES	Director name
writer	String / VARCHAR(255)	YES	Writer name
star	String / VARCHAR(255)	YES	Lead actor/star name
country	String / VARCHAR(100)	YES	Country of origin
budget	Float64 / DOUBLE	YES	Production budget (USD)
gross	Float64 / DOUBLE	YES	Box office gross revenue (USD)
company	String / VARCHAR(255)	YES	Production company
runtime	Int32 / INT	YES	Runtime in minutes
sentiment_positive	Float64 / DOUBLE	YES	Positive sentiment score (e.g. from reviews)
sentiment_neutral	Float64 / DOUBLE	YES	Neutral sentiment score
sentiment_negative	Float64 / DOUBLE	YES	Negative sentiment score
Example DDL (ClickHouse)
sql
CREATE TABLE movie_trailers
(
    name                 String,
    video_id             String,
    rating               String,
    genre                String,
    year                 UInt16,
    released             String,
    votes                UInt64,
    director             String,
    writer               String,
    star                 String,
    country              String,
    budget               Float64,
    gross                Float64,
    company              String,
    runtime              UInt32,
    sentiment_positive   Float64,
    sentiment_neutral    Float64,
    sentiment_negative   Float64
)
ENGINE = MergeTree()
ORDER BY (name, year);
Example DDL (MySQL / PostgreSQL)
sql
CREATE TABLE movie_trailers (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(255) NOT NULL,
    video_id             VARCHAR(50) NOT NULL,
    rating               VARCHAR(10),
    genre                VARCHAR(100),
    year                 SMALLINT,
    released             VARCHAR(150),
    votes                BIGINT,
    director             VARCHAR(255),
    writer               VARCHAR(255),
    star                 VARCHAR(255),
    country              VARCHAR(100),
    budget               DOUBLE PRECISION,
    gross                DOUBLE PRECISION,
    company              VARCHAR(255),
    runtime              INT,
    sentiment_positive   DOUBLE PRECISION,
    sentiment_neutral    DOUBLE PRECISION,
    sentiment_negative   DOUBLE PRECISION
);
Notes
client.insert_df(...) suggests a ClickHouse client — the ClickHouse DDL above matches column order in your df1[[...]] selection.
released is kept as a String/VARCHAR here since raw IMDB-style datasets often store it as "June 13, 1980 (United States)" rather than a clean date — parse into a proper DATE column later if needed.
sentiment_positive, sentiment_neutral, sentiment_negative look like they sum to ~1.0 (a probability distribution) — worth adding a CHECK constraint in Postgres/MySQL if you want to enforce that.
If video_id should be unique per row, add a UNIQUE constraint or use it as part of a composite key.
No id/primary key column was in your df1 selection — added an auto-increment id in the SQL version since ClickHouse doesn't require one but relational DBs typically do.
Content
