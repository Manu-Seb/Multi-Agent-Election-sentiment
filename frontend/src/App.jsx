import React, { useMemo, useRef, useState } from 'react'

const DEFAULT_STREAM_BASE = import.meta.env.VITE_STREAM_BASE || 'http://localhost:8040/stream'

function formatNumber(value) {
  if (typeof value !== 'number') return value
  return Number.isInteger(value) ? String(value) : value.toFixed(4)
}

function prettify(data) {
  return JSON.stringify(data, null, 2)
}

function ArticleCard({ article }) {
  return (
    <article className="article-card">
      <header className="article-header">
        <div>
          <h4>{article.title || `Article ${article.id}`}</h4>
          <p className="article-meta">
            id={article.id} · {article.published_at || 'unknown time'} · {article.accepted ? 'accepted' : 'rejected'}
          </p>
        </div>
        <span className={article.accepted ? 'pill ok' : 'pill skip'}>
          {article.accepted ? 'Accepted' : 'Rejected'}
        </span>
      </header>

      {article.preview ? <p className="preview">{article.preview}</p> : null}

      <details>
        <summary>Processing Details</summary>
        <div className="details-grid">
          <section>
            <h5>Step 1: Filter</h5>
            <pre>{prettify(article.step1 || {})}</pre>
          </section>
          <section>
            <h5>Step 2: NER + Sentiment</h5>
            <pre>{prettify(article.step2 || {})}</pre>
          </section>
          <section>
            <h5>Step 3: Graph Change</h5>
            <pre>{prettify(article.step3 || {})}</pre>
          </section>
          <section>
            <h5>Step 4: Current State Snapshot</h5>
            <pre>{prettify(article.step4 || {})}</pre>
          </section>
          {article.timeout ? (
            <section>
              <h5>Timeout</h5>
              <pre>{prettify(article.timeout)}</pre>
            </section>
          ) : null}
          {article.error ? (
            <section>
              <h5>Error</h5>
              <pre>{prettify(article.error)}</pre>
            </section>
          ) : null}
        </div>
      </details>
    </article>
  )
}

function DemoButton({ onEmit }) {
  function runDemo() {
    const id = String(Date.now())
    onEmit({ type: 'status', message: 'Demo event stream started' })
    onEmit({
      type: 'step1',
      article_id: id,
      accepted: true,
      title: 'Israel launches preventive strike in Tehran',
      published_at: new Date().toISOString(),
      source: 'Demo Source',
      link: 'https://example.com/demo',
      article_preview: 'Demo article body for UI testing.'
    })
    setTimeout(() =>
      onEmit({
        type: 'step2',
        article_id: id,
        article_sentiment_mean: -0.4221,
        entities: [
          { text: 'Israel', type: 'GPE', sentiment: -0.23, confidence: 0.98, sentence: 'Israel launched strike.' },
          { text: 'Tehran', type: 'GPE', sentiment: -0.61, confidence: 0.96, sentence: 'Explosion heard in Tehran.' }
        ]
      }),
    300)
    setTimeout(() =>
      onEmit({
        type: 'step3',
        article_id: id,
        node_count: 12,
        edge_count: 18,
        entity_changes: [{ id: 'Israel', delta_mentions: 1, new_sentiment: -0.21 }],
        relationship_changes: [{ source: 'Israel', target: 'Tehran', delta_strength: 0.5, new_joint_sentiment: -0.4 }]
      }),
    700)
    setTimeout(() =>
      onEmit({
        type: 'step4',
        article_id: id,
        total_nodes: 12,
        total_edges: 18,
        nodes: [
          { id: 'Israel', type: 'GPE', mention_count: 7, sentiment: -0.21, centrality: 0.7 },
          { id: 'Tehran', type: 'GPE', mention_count: 4, sentiment: -0.54, centrality: 0.55 }
        ],
        edges: [{ source: 'Israel', target: 'Tehran', strength: 2.1, joint_sentiment: -0.4 }]
      }),
    1200)
  }

  return (
    <button type="button" className="secondary" onClick={runDemo}>
      Run Demo Event
    </button>
  )
}

export default function App() {
  const [topicInput, setTopicInput] = useState('')
  const [topic, setTopic] = useState('')
  const [streamBase, setStreamBase] = useState(DEFAULT_STREAM_BASE)
  const [status, setStatus] = useState('Idle')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [articlesById, setArticlesById] = useState({})
  const [articleOrder, setArticleOrder] = useState([])
  const [latestState, setLatestState] = useState(null)
  const [connection, setConnection] = useState('disconnected')
  const sourceRef = useRef(null)

  const history = useMemo(() => articleOrder.map((id) => articlesById[id]).filter(Boolean), [articleOrder, articlesById])
  const currentArticle = history[history.length - 1] || null

  function closeStream() {
    if (sourceRef.current) {
      sourceRef.current.close()
      sourceRef.current = null
    }
    setConnection('disconnected')
  }

  function resetData() {
    setArticlesById({})
    setArticleOrder([])
    setLatestState(null)
  }

  function applyEvent(data) {
    if (data.type === 'status') {
      setStatus(data.message)
      return
    }

    const articleId = String(data.article_id || '')
    if (!articleId) return

    setArticlesById((prev) => {
      const existing = prev[articleId] || { id: articleId }
      const next = { ...existing }

      if (data.type === 'step1') {
        next.accepted = !!data.accepted
        next.title = data.title || next.title
        next.published_at = data.published_at || next.published_at
        next.preview = data.article_preview || next.preview
        next.step1 = data
      }
      if (data.type === 'step2') next.step2 = data
      if (data.type === 'step3') next.step3 = data
      if (data.type === 'step4') {
        next.step4 = data
        setLatestState(data)
      }
      if (data.type === 'timeout') next.timeout = data
      if (data.type === 'error') next.error = data

      return { ...prev, [articleId]: next }
    })

    setArticleOrder((prev) => (prev.includes(articleId) ? prev : [...prev, articleId]))
  }

  function startStream(e, forcedTopic = null) {
    if (e && typeof e.preventDefault === 'function') e.preventDefault()
    const t = (forcedTopic || topicInput).trim()
    if (!t) return

    closeStream()
    resetData()
    setTopic(t)
    setStatus(`Connecting stream for topic '${t}'...`)

    const url = `${streamBase}?topic=${encodeURIComponent(t)}`
    const es = new EventSource(url)
    sourceRef.current = es
    setConnection('connecting')

    es.onopen = () => {
      setConnection('connected')
      setStatus(`Connected: ${url}`)
    }

    es.onmessage = (evt) => {
      const raw = String(evt.data || '').trim()
      if (!raw || !raw.startsWith('{')) {
        return
      }
      try {
        const data = JSON.parse(raw)
        applyEvent(data)
      } catch {
        // Ignore malformed frames silently; keep the stream running.
        return
      }
    }

    es.onerror = () => {
      setConnection('error')
      setStatus('Stream connection dropped or endpoint unavailable')
    }
  }

  return (
    <div className="app">
      {!topic ? (
        <section className="topic-gate">
          <h1>Election Sentiment Monitor</h1>
          <p>Enter topic to start live pipeline view.</p>
          <form onSubmit={startStream} className="gate-form">
            <input value={topicInput} onChange={(e) => setTopicInput(e.target.value)} placeholder="e.g. israel" required />
            <button type="submit">Start Tracking</button>
          </form>
        </section>
      ) : (
        <>
          <header className="topbar">
            <div>
              <h1>Election Sentiment Monitor</h1>
              <p>Topic: <strong>{topic}</strong></p>
            </div>
            <span className={`pill ${connection === 'connected' ? 'ok' : connection === 'connecting' ? 'warn' : 'err'}`}>
              {connection}
            </span>
          </header>

          <section className="panels">
            <div className="panel">
              <h2>Options</h2>
              <label>Stream URL</label>
              <input value={streamBase} onChange={(e) => setStreamBase(e.target.value)} />
              <div className="option-buttons">
                <button onClick={(e) => startStream(e, topic)}>Reconnect</button>
                <button type="button" className="secondary" onClick={closeStream}>Stop Stream</button>
                <button type="button" className="secondary" onClick={resetData}>Clear View</button>
                <DemoButton onEmit={applyEvent} />
              </div>
              <p className="status-line">{status}</p>
            </div>

            <div className="panel">
              <h2>Current State</h2>
              {!latestState ? (
                <p>No graph state yet.</p>
              ) : (
                <>
                  <p>Total Nodes: <strong>{latestState.total_nodes}</strong> · Total Edges: <strong>{latestState.total_edges}</strong></p>
                  <h3>Top Nodes</h3>
                  <table>
                    <thead><tr><th>Id</th><th>Type</th><th>Mentions</th><th>Sentiment</th><th>Centrality</th></tr></thead>
                    <tbody>
                      {(latestState.nodes || []).map((n) => (
                        <tr key={n.id}><td>{n.id}</td><td>{n.type}</td><td>{formatNumber(n.mention_count)}</td><td>{formatNumber(n.sentiment)}</td><td>{formatNumber(n.centrality)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <h3>Top Edges</h3>
                  <table>
                    <thead><tr><th>Source</th><th>Target</th><th>Strength</th><th>Joint Sentiment</th></tr></thead>
                    <tbody>
                      {(latestState.edges || []).map((e, i) => (
                        <tr key={`${e.source}-${e.target}-${i}`}><td>{e.source}</td><td>{e.target}</td><td>{formatNumber(e.strength)}</td><td>{formatNumber(e.joint_sentiment)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  <details>
                    <summary>Raw Current State Payload</summary>
                    <pre>{prettify(latestState)}</pre>
                  </details>
                </>
              )}
            </div>

            <div className="panel">
              <h2>Current Article Status</h2>
              {!currentArticle ? (
                <p>No processed article yet.</p>
              ) : (
                <ArticleCard article={currentArticle} />
              )}
            </div>
          </section>

          <button className="history-toggle" onClick={() => setHistoryOpen((v) => !v)}>
            {historyOpen ? 'Hide History' : 'Show History'} ({history.length})
          </button>

          {historyOpen ? (
            <section className="history">
              {history.length === 0 ? <p>No history yet.</p> : history.slice().reverse().map((a) => <ArticleCard key={a.id} article={a} />)}
            </section>
          ) : null}
        </>
      )}
    </div>
  )
}
