CREATE TABLE IF NOT EXISTS graph_change_events (
    id BIGSERIAL PRIMARY KEY,
    event_time TIMESTAMPTZ(3) NOT NULL,
    article_id VARCHAR(255) NOT NULL,
    changes JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_change_events_event_time ON graph_change_events (event_time);
CREATE INDEX IF NOT EXISTS idx_graph_change_events_article_id ON graph_change_events (article_id);
CREATE INDEX IF NOT EXISTS idx_graph_change_events_changes_gin ON graph_change_events USING GIN (changes);

CREATE TABLE IF NOT EXISTS graph_snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_time TIMESTAMPTZ(3) NOT NULL,
    last_event_id BIGINT REFERENCES graph_change_events(id),
    graph_state JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_snapshots_snapshot_time ON graph_snapshots (snapshot_time);
