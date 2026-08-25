import { useEffect, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type ReactFlowInstance,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './exposureMap.css'

type Article = {
  id: number
  title: string
  summary: string
  source: string
  source_url: string
  published_at: string
  evidence_score: number | null
}

type GraphNode = {
  id: string
  type: 'article' | 'sector' | 'industry' | 'company' | 'position'
  label: string
  subtitle?: string
  direction?: 'positive' | 'negative' | 'unclear'
  evidence_score?: number | null
  dte?: number | null
  quantity?: number | null
  paper?: boolean
  position_state?: 'historical_live'
  recorded_at?: string
  outcome_pnl_pct?: number | null
  outcome_updated_at?: string
  outcome_label?: string
  learned_reason?: string
  risk_score?: number | null
  risk_level?: string
  reason?: string
  score_inputs?: Record<string, unknown> | null
}

type GraphEdge = {
  id: string
  source: string
  target: string
  kind: 'evidence' | 'hierarchy' | 'position'
  label: string
  score: number
  direction?: 'positive' | 'negative' | 'unclear'
  confidence?: string
  magnitude?: string
  score_inputs?: Record<string, unknown> | null
}

type PositionImpact = {
  option_symbol: string
  risk_level: string
  reason: string
  risk_score: number
  score_inputs?: Record<string, unknown> | null
}

type HistoricalLiveImpact = PositionImpact & {
  execution_id: number
  manager_decision_history_id: number
  underlying_symbol: string
  dte?: number | null
  quantity?: number | null
  recorded_at?: string
  outcome_pnl_pct?: number | null
  outcome_updated_at?: string
  outcome_label?: string
  learned_reason?: string
}

type ExposurePayload = {
  article_id: number
  has_run: boolean
  article: Article
  run?: {
    id: number
    model: string
    prompt_version: string
    created_at: string
    article_summary: string
  }
  nodes: GraphNode[]
  edges: GraphEdge[]
  position_impacts: PositionImpact[]
  historical_live_impacts?: HistoricalLiveImpact[]
  uncertainties: string[]
  no_trade_executed: boolean
  demo_data?: boolean
}

type DemoScenario = { demo_mode: true; disclaimer: string; exposure: { articles: Article[]; payload: ExposurePayload } }

type ExposureNodeData = GraphNode
type FlowNode = Node<ExposureNodeData, 'exposure'>
type FlowEdge = Edge<{ label: string; score: number }>
type DirectionFilter = 'all' | 'positive' | 'negative' | 'unclear'
type RiskFilter = 'all' | 'high' | 'medium' | 'low'

const columns: Record<GraphNode['type'], number> = {
  article: 0,
  sector: 290,
  industry: 560,
  company: 830,
  position: 1100,
}

function formatDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return 'Unknown time'
  }
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}

function formatEvidenceScore(value: number | null | undefined): string {
  return value === null || value === undefined ? 'N/A' : value.toFixed(2)
}

function formatPnl(value: number | null | undefined): string {
  return value === null || value === undefined || Number.isNaN(value) ? 'Outcome pending' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}% P/L`
}

function scoreColor(direction?: string): string {
  if (direction === 'negative') {
    return '#dd5a63'
  }
  if (direction === 'positive') {
    return '#46a37a'
  }
  return '#7d899d'
}

function ExposureNode({ data }: NodeProps<FlowNode>) {
  const direction = data.direction ?? 'unclear'
  const historicalLive = data.position_state === 'historical_live'
  return (
    <div className={`exposure-node exposure-node--${data.type} exposure-node--${direction}${historicalLive ? ' exposure-node--historical-live' : ''}`}>
      <p className="exposure-node__type">{data.type === 'position' ? (historicalLive ? 'Live Trade Outcome' : 'Paper Position') : data.type}</p>
      <strong>{data.label}</strong>
      {data.subtitle ? <span>{data.subtitle}</span> : null}
      {data.type === 'article' ? <small>Evidence {formatEvidenceScore(data.evidence_score)}</small> : null}
      {data.type === 'position' ? (
        <small>{data.dte ?? 'N/A'} DTE{historicalLive ? ' at execution' : ''} · {data.quantity ?? 0} contracts · {historicalLive ? 'recorded live trade' : 'paper'}</small>
      ) : null}
      {historicalLive ? <b>{formatPnl(data.outcome_pnl_pct)}{data.outcome_label ? ` · ${data.outcome_label.replaceAll('_', ' ')}` : ''}</b> : null}
      {data.type === 'position' && !historicalLive && data.risk_score !== undefined ? <b>Risk {data.risk_score}</b> : null}
    </div>
  )
}

const nodeTypes = { exposure: ExposureNode }

async function getArticles(): Promise<Article[]> {
  const response = await fetch('/api/exposure-map/articles?limit=50')
  if (!response.ok) {
    throw new Error(`Failed to load articles: ${response.status}`)
  }
  const payload = await response.json() as { articles: Article[] }
  return payload.articles
}

async function getExposureMap(articleId: number): Promise<ExposurePayload> {
  const response = await fetch(`/api/exposure-map/${articleId}`)
  if (!response.ok) {
    throw new Error(`Failed to load exposure map: ${response.status}`)
  }
  return response.json() as Promise<ExposurePayload>
}

async function analyzeArticle(articleId: number, model: string): Promise<ExposurePayload> {
  const response = await fetch('/api/exposure-map/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ article_id: articleId, ...(model.trim() ? { model: model.trim() } : {}) }),
  })
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Administrative access is required. Open “Unlock protected actions,” sign in, then try again.')
    }
    const payload = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(payload?.error ?? `Failed to analyze article: ${response.status}`)
  }
  return response.json() as Promise<ExposurePayload>
}

async function getDemoScenario(): Promise<DemoScenario> {
  const response = await fetch('/api/demo-mode')
  if (!response.ok) throw new Error(`Failed to load demo mode: ${response.status}`)
  return response.json() as Promise<DemoScenario>
}

function buildFlowNodes(nodes: GraphNode[]): FlowNode[] {
  const perTypeIndex: Record<GraphNode['type'], number> = { article: 0, sector: 0, industry: 0, company: 0, position: 0 }
  return nodes.map((node) => {
    const row = perTypeIndex[node.type]
    perTypeIndex[node.type] += 1
    return {
      id: node.id,
      type: 'exposure',
      position: { x: columns[node.type], y: 60 + row * 152 },
      data: node,
    }
  })
}

function buildFlowEdges(edges: GraphEdge[]): FlowEdge[] {
  return edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    markerEnd: { type: MarkerType.ArrowClosed, color: scoreColor(edge.direction) },
    style: {
      stroke: scoreColor(edge.direction),
      strokeWidth: Math.max(1.5, Math.min(8, 1.5 + edge.score / 16)),
    },
    labelStyle: { fill: '#c4ccda', fontSize: 11, fontWeight: 600 },
    labelBgStyle: { fill: '#171c28', fillOpacity: 0.92 },
    labelBgPadding: [5, 3],
    data: { label: edge.label, score: edge.score },
  }))
}

function buildVisibleGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  directionFilter: DirectionFilter,
  riskFilter: RiskFilter,
  positionPathsOnly: boolean,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  if (directionFilter === 'all' && riskFilter === 'all' && !positionPathsOnly) {
    return { nodes, edges }
  }

  const incomingEdges = new Map<string, GraphEdge[]>()
  const outgoingEdges = new Map<string, GraphEdge[]>()
  for (const edge of edges) {
    incomingEdges.set(edge.target, [...(incomingEdges.get(edge.target) ?? []), edge])
    outgoingEdges.set(edge.source, [...(outgoingEdges.get(edge.source) ?? []), edge])
  }

  const visibleNodeIds = new Set<string>()
  const includeAncestors = (nodeId: string) => {
    if (visibleNodeIds.has(nodeId)) {
      return
    }
    visibleNodeIds.add(nodeId)
    for (const edge of incomingEdges.get(nodeId) ?? []) {
      includeAncestors(edge.source)
    }
  }
  const descendedNodeIds = new Set<string>()
  const includeDescendants = (nodeId: string) => {
    if (descendedNodeIds.has(nodeId)) {
      return
    }
    descendedNodeIds.add(nodeId)
    visibleNodeIds.add(nodeId)
    for (const edge of outgoingEdges.get(nodeId) ?? []) {
      includeDescendants(edge.target)
    }
  }

  if (directionFilter !== 'all') {
    nodes
      .filter((node) => node.type !== 'article' && node.direction === directionFilter)
      .forEach((node) => {
        includeAncestors(node.id)
        includeDescendants(node.id)
      })
  }

  if (positionPathsOnly || riskFilter !== 'all') {
    nodes
      .filter(
        (node) =>
          node.type === 'position' &&
          (riskFilter === 'all' || node.risk_level?.toLowerCase() === riskFilter),
      )
      .forEach((node) => includeAncestors(node.id))
  }

  const visibleNodes = nodes.filter((node) => visibleNodeIds.has(node.id))
  return {
    nodes: visibleNodes,
    edges: edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)),
  }
}

function ExposureMapPage({ demoMode = false }: { demoMode?: boolean }) {
  const [articles, setArticles] = useState<Article[]>([])
  const [selectedArticleId, setSelectedArticleId] = useState<number | null>(null)
  const [payload, setPayload] = useState<ExposurePayload | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [modelOverride, setModelOverride] = useState('')
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>('all')
  const [riskFilter, setRiskFilter] = useState<RiskFilter>('all')
  const [positionPathsOnly, setPositionPathsOnly] = useState(false)
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null)

  useEffect(() => {
    let active = true
    const load = demoMode
      ? getDemoScenario().then((scenario) => ({ articles: scenario.exposure.articles, payload: scenario.exposure.payload }))
      : getArticles().then((nextArticles) => ({ articles: nextArticles, payload: null }))
    load
      .then(({ articles: nextArticles, payload: nextPayload }) => {
        if (!active) {
          return
        }
        setArticles(nextArticles)
        setSelectedArticleId(nextArticles[0]?.id ?? null)
        if (nextPayload) {
          setPayload(nextPayload)
          setSelectedNode(
            nextPayload.nodes.find((node) => node.type === 'position' && node.risk_level === 'high')
              ?? nextPayload.nodes.find((node) => node.type === 'article')
              ?? null,
          )
        }
        setError(null)
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load articles.')
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })
    return () => { active = false }
  }, [demoMode])

  useEffect(() => {
    if (demoMode) {
      return
    }
    if (selectedArticleId === null) {
      setPayload(null)
      return
    }
    let active = true
    setLoading(true)
    getExposureMap(selectedArticleId)
      .then((nextPayload) => {
        if (active) {
          setPayload(nextPayload)
          setSelectedNode(nextPayload.nodes.find((node) => node.type === 'article') ?? null)
          setError(null)
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load exposure map.')
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })
    return () => { active = false }
  }, [demoMode, selectedArticleId])

  const visibleGraph = useMemo(
    () => buildVisibleGraph(payload?.nodes ?? [], payload?.edges ?? [], directionFilter, riskFilter, positionPathsOnly),
    [payload, directionFilter, riskFilter, positionPathsOnly],
  )
  const flowNodes = useMemo(() => buildFlowNodes(visibleGraph.nodes), [visibleGraph.nodes])
  const flowEdges = useMemo(() => buildFlowEdges(visibleGraph.edges), [visibleGraph.edges])
  const selectedArticle = articles.find((article) => article.id === selectedArticleId) ?? payload?.article

  const startAnalysis = () => {
    if (selectedArticleId === null) {
      return
    }
    setAnalyzing(true)
    analyzeArticle(selectedArticleId, modelOverride)
      .then((nextPayload) => {
        setPayload(nextPayload)
        setSelectedNode(nextPayload.nodes.find((node) => node.type === 'article') ?? null)
        setError(null)
      })
      .catch((analysisError: unknown) => {
        setError(analysisError instanceof Error ? analysisError.message : 'Failed to analyze article.')
      })
      .finally(() => setAnalyzing(false))
  }

  const resetFilters = () => {
    setDirectionFilter('all')
    setRiskFilter('all')
    setPositionPathsOnly(false)
  }

  const fitGraph = () => {
    flowInstance?.fitView({ padding: 0.2, duration: 250 })
  }

  return (
    <main className="exposure-map">
      <aside className="exposure-map__articles">
        <div className="exposure-map__section-heading">
          <p>Evidence Queue</p>
          <h2>Recent news</h2>
          {demoMode ? <span className="exposure-map__demo-badge">Seeded demo</span> : null}
        </div>
        {articles.length === 0 && !loading ? <p className="exposure-map__empty">No scored articles are available yet.</p> : null}
        <div className="exposure-map__article-list">
          {articles.map((article) => (
            <button
              key={article.id}
              type="button"
              className={`exposure-map__article${article.id === selectedArticleId ? ' exposure-map__article--active' : ''}`}
              onClick={() => setSelectedArticleId(article.id)}
            >
              <span className="exposure-map__article-score">Evidence {formatEvidenceScore(article.evidence_score)}</span>
              <strong>{article.title}</strong>
              <small>{article.source || 'Unknown source'} · {formatDate(article.published_at)}</small>
            </button>
          ))}
        </div>
      </aside>

      <section className="exposure-map__canvas-panel">
        <div className="exposure-map__canvas-header">
          <div>
            <p>Grounded Exposure Map</p>
            <h2>{selectedArticle?.title ?? 'Select an article'}</h2>
          </div>
          <div className="exposure-map__analysis-controls">
            {demoMode ? <span className="exposure-map__demo-copy">Cached walkthrough · no model call</span> : <><input
              aria-label="Model override"
              value={modelOverride}
              placeholder="Configured model"
              onChange={(event) => setModelOverride(event.target.value)}
            />
            <a
              className="exposure-map__unlock"
              href="/admin-login#/exposure-map"
              target="_blank"
              rel="noreferrer"
              title="Open the browser sign-in prompt for protected actions"
            >
              Unlock protected actions
            </a>
            <button type="button" className="exposure-map__analyze" onClick={startAnalysis} disabled={analyzing || selectedArticleId === null}>
              {analyzing ? 'Analyzing…' : payload?.has_run ? 'Use cached analysis' : 'Analyze article'}
            </button></>}
          </div>
        </div>
        <div className="exposure-map__graph-toolbar" aria-label="Exposure map filters and controls">
          <div className="exposure-map__filter-group">
            <span className="exposure-map__filter-label">Impact</span>
            {(['all', 'positive', 'negative', 'unclear'] as DirectionFilter[]).map((filter) => (
              <button
                key={filter}
                type="button"
                className={`exposure-map__filter${directionFilter === filter ? ' exposure-map__filter--active' : ''}`}
                onClick={() => setDirectionFilter(filter)}
              >
                {filter}
              </button>
            ))}
          </div>
          <div className="exposure-map__filter-group">
            <label className="exposure-map__filter-label" htmlFor="exposure-risk-filter">Position risk</label>
            <select id="exposure-risk-filter" value={riskFilter} onChange={(event) => setRiskFilter(event.target.value as RiskFilter)}>
              <option value="all">All</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <label className="exposure-map__toggle">
              <input type="checkbox" checked={positionPathsOnly} onChange={(event) => setPositionPathsOnly(event.target.checked)} />
              Position paths
            </label>
          </div>
          <div className="exposure-map__graph-actions">
            <button type="button" onClick={fitGraph} disabled={!flowInstance || flowNodes.length === 0}>Fit graph</button>
            <button type="button" onClick={resetFilters}>Reset filters</button>
          </div>
        </div>
        <div className="exposure-map__legend" aria-label="Exposure map legend">
          <span><i className="exposure-map__legend-dot exposure-map__legend-dot--article" />Article</span>
          <span><i className="exposure-map__legend-dot exposure-map__legend-dot--positive" />Positive impact</span>
          <span><i className="exposure-map__legend-dot exposure-map__legend-dot--negative" />Negative impact</span>
          <span><i className="exposure-map__legend-dot exposure-map__legend-dot--unclear" />Unclear impact</span>
          <span><i className="exposure-map__legend-dot exposure-map__legend-dot--live-history" />Recorded live outcome</span>
          <span><i className="exposure-map__legend-line" />Thicker edge = higher deterministic exposure</span>
        </div>
        {error ? <p className="exposure-map__error">{error}</p> : null}
        {!loading && payload && !payload.has_run ? <div className="exposure-map__empty">Select “Analyze article” to generate and cache grounded explanations.</div> : null}
        {loading ? <div className="exposure-map__empty">Loading evidence map…</div> : null}
        {payload?.has_run ? (
          <div className="exposure-map__flow">
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.3}
              onInit={setFlowInstance}
              onNodeClick={(_, node) => setSelectedNode(node.data as GraphNode)}
            >
              <Background color="#3d4658" gap={22} size={1} />
              <Controls showFitView={false} />
            </ReactFlow>
          </div>
        ) : null}
      </section>

      <aside className="exposure-map__drawer">
        <div className="exposure-map__safety">No trade is executed by this feature.</div>
        {demoMode ? <div className="exposure-map__demo-notice">Seeded fictional scenario for demos. No live news, positions, or orders are used.</div> : null}
        <p className="exposure-map__drawer-eyebrow">Why this matters</p>
        <h2>{selectedNode?.label ?? selectedArticle?.title ?? 'No selection'}</h2>
        {selectedNode?.reason ? <p>{selectedNode.reason}</p> : null}
        {payload?.run ? <p>{payload.run.article_summary}</p> : null}
        {selectedArticle?.source_url ? <a href={selectedArticle.source_url} target="_blank" rel="noreferrer">Open source: {selectedArticle.source || 'article'}</a> : null}
        {selectedNode?.type === 'position' ? (
          <div className="exposure-map__drawer-block">
            {selectedNode.position_state === 'historical_live' ? <>
              <h3>Recorded live outcome</h3>
              <p>{formatPnl(selectedNode.outcome_pnl_pct)}{selectedNode.outcome_label ? ` · ${selectedNode.outcome_label.replaceAll('_', ' ')}` : ''}</p>
              <p>Executed {selectedNode.recorded_at ? formatDate(selectedNode.recorded_at) : 'at an unknown time'} · {selectedNode.dte ?? 'N/A'} DTE at execution</p>
              {selectedNode.learned_reason ? <p>{selectedNode.learned_reason}</p> : null}
            </> : <>
              <h3>Deterministic score</h3>
              <p>Risk {selectedNode.risk_score ?? 0} · {selectedNode.risk_level ?? 'low'} · {selectedNode.dte ?? 'N/A'} DTE</p>
              <p>{String(selectedNode.score_inputs?.formula ?? 'Position exposure × magnitude × confidence × DTE/volatility multiplier')}</p>
            </>}
          </div>
        ) : null}
        <div className="exposure-map__drawer-block">
          <h3>Risk factors</h3>
          {payload?.position_impacts.length ? (
            <ul>
              {payload.position_impacts.map((impact) => <li key={impact.option_symbol}><strong>{impact.option_symbol}</strong> · {impact.risk_level} ({impact.risk_score})</li>)}
            </ul>
          ) : <p>No current paper positions are connected to this article.</p>}
        </div>
        <div className="exposure-map__drawer-block">
          <h3>Learned live outcomes</h3>
          {payload?.historical_live_impacts?.length ? (
            <ul>
              {payload.historical_live_impacts.map((impact) => <li key={impact.execution_id}><strong>{impact.option_symbol}</strong> · {formatPnl(impact.outcome_pnl_pct)}{impact.outcome_label ? ` · ${impact.outcome_label.replaceAll('_', ' ')}` : ''}</li>)}
            </ul>
          ) : <p>No linked non-paper execution outcomes are affected by this article.</p>}
        </div>
        <div className="exposure-map__drawer-block">
          <h3>Uncertainty</h3>
          {payload?.uncertainties.length ? <ul>{payload.uncertainties.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No additional uncertainty was recorded.</p>}
        </div>
        {payload?.run ? <small>Model {payload.run.model} · {payload.run.prompt_version} · {formatDate(payload.run.created_at)}</small> : null}
      </aside>
    </main>
  )
}

export default ExposureMapPage
