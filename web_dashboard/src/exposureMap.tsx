import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { formatDateTime, formatDecimal, formatSignedPercent, sentenceCase } from './format'
import { chartTheme } from './ui/chartTheme'
import { Panel } from './ui/Panel'
import { Segmented } from './ui/Segmented'
import { Select } from './ui/Select'

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
  return formatDateTime(value, { placeholder: 'Unknown time' })
}

function formatEvidenceScore(value: number | null | undefined): string {
  return formatDecimal(value)
}

function formatPnl(value: number | null | undefined): string {
  return formatSignedPercent(value, { placeholder: 'Outcome pending', suffix: ' P/L' })
}

function scoreColor(direction?: string): string {
  if (direction === 'negative') {
    return chartTheme.loss
  }
  if (direction === 'positive') {
    return chartTheme.gain
  }
  return chartTheme.neutral
}

/** Articles and positions keep their own outline; everything else is colored by impact. */
function nodeBorder(data: ExposureNodeData): string {
  if (data.type === 'article') return 'border-info'
  if (data.type === 'position') return 'border-warning'
  if (data.direction === 'positive') return 'border-gain'
  if (data.direction === 'negative') return 'border-loss'
  return 'border-[#6e6e76]'
}

function nodeKind(data: ExposureNodeData, historicalLive: boolean): string {
  if (data.type !== 'position') return sentenceCase(data.type)
  return historicalLive ? 'Live trade outcome' : 'Paper position'
}

function ExposureNode({ data }: NodeProps<FlowNode>) {
  const historicalLive = data.position_state === 'historical_live'
  const width = data.type === 'article' ? 'w-[235px]' : 'w-[190px]'
  return (
    <div className={`grid gap-1 rounded-md border-2 bg-raised p-3 text-base-content in-[.selected]:outline-2 in-[.selected]:outline-offset-2 in-[.selected]:outline-primary ${width} ${nodeBorder(data)} ${historicalLive ? 'border-dashed' : ''}`}>
      {/* Edges attach to handles; without them React Flow draws no edges at all. */}
      <Handle type="target" position={Position.Left} className="opacity-0" isConnectable={false} />
      <p className="text-[11px] text-dim">{nodeKind(data, historicalLive)}</p>
      <strong className="truncate">{data.label}</strong>
      {data.subtitle ? <span className="text-[11px] text-ink-2">{data.subtitle}</span> : null}
      {data.type === 'article' ? <small className="text-[11px] text-ink-2">Evidence {formatEvidenceScore(data.evidence_score)}</small> : null}
      {data.type === 'position' ? (
        <small className="text-[11px] text-ink-2">{data.dte ?? 'N/A'} DTE{historicalLive ? ' at execution' : ''} · {data.quantity ?? 0} contracts · {historicalLive ? 'recorded live trade' : 'paper'}</small>
      ) : null}
      {historicalLive ? <b className="text-[11px]">{formatPnl(data.outcome_pnl_pct)}{data.outcome_label ? ` · ${data.outcome_label.replaceAll('_', ' ')}` : ''}</b> : null}
      {data.type === 'position' && !historicalLive && data.risk_score !== undefined ? <b className="text-[11px]">Risk {data.risk_score}</b> : null}
      <Handle type="source" position={Position.Right} className="opacity-0" isConnectable={false} />
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
    labelStyle: { fill: chartTheme.ink, fontSize: 12, fontWeight: 600 },
    labelBgStyle: { fill: chartTheme.panel, fillOpacity: 0.92 },
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


const IMPACT_OPTIONS: { value: DirectionFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'positive', label: 'Positive' },
  { value: 'negative', label: 'Negative' },
  { value: 'unclear', label: 'Unclear' },
]

const RISK_OPTIONS: { value: RiskFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]

const LEGEND: { label: string; dot: string }[] = [
  { label: 'Article', dot: 'border-info' },
  { label: 'Positive impact', dot: 'border-gain' },
  { label: 'Negative impact', dot: 'border-loss' },
  { label: 'Unclear impact', dot: 'border-[#6e6e76]' },
  { label: 'Recorded live outcome', dot: 'border-dashed border-warning' },
]

function DrawerBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-base-300 pt-3">
      <h3 className="mb-1 text-xs font-semibold">{title}</h3>
      {children}
    </div>
  )
}

function firstNodeOf(payload: ExposurePayload): GraphNode | null {
  return payload.nodes.find((node) => node.type === 'article') ?? null
}

function ExposureMapPage({ demoMode = false }: { demoMode?: boolean }) {
  const [articles, setArticles] = useState<Article[]>([])
  const [articlesLoaded, setArticlesLoaded] = useState(false)
  const [selectedArticleId, setSelectedArticleId] = useState<number | null>(null)
  const [payload, setPayload] = useState<ExposurePayload | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [error, setError] = useState<string | null>(null)
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
              ?? firstNodeOf(nextPayload),
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
          setArticlesLoaded(true)
        }
      })
    return () => { active = false }
  }, [demoMode])

  useEffect(() => {
    if (demoMode || selectedArticleId === null) {
      return
    }
    let active = true
    getExposureMap(selectedArticleId)
      .then((nextPayload) => {
        if (active) {
          setPayload(nextPayload)
          setSelectedNode(firstNodeOf(nextPayload))
          setError(null)
        }
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load exposure map.')
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
  // The map on screen belongs to another article until the picked one's arrives.
  const mapLoading = !error && (!articlesLoaded || (!demoMode && selectedArticleId !== null && payload?.article_id !== selectedArticleId))
  const graphReady = Boolean(payload?.has_run) && !mapLoading

  function pickArticle(articleId: number) {
    setSelectedArticleId(articleId)
    setError(null)
  }

  function startAnalysis() {
    if (selectedArticleId === null) {
      return
    }
    setAnalyzing(true)
    analyzeArticle(selectedArticleId, modelOverride)
      .then((nextPayload) => {
        setPayload(nextPayload)
        setSelectedNode(firstNodeOf(nextPayload))
        setError(null)
      })
      .catch((analysisError: unknown) => {
        setError(analysisError instanceof Error ? analysisError.message : 'Failed to analyze article.')
      })
      .finally(() => setAnalyzing(false))
  }

  function resetFilters() {
    setDirectionFilter('all')
    setRiskFilter('all')
    setPositionPathsOnly(false)
  }

  const analysisControls = demoMode ? <span className="text-[11px] text-dim">Cached walkthrough · no model call</span> : (
    <>
      <input
        aria-label="Model override"
        className="input input-xs w-40 font-mono"
        value={modelOverride}
        placeholder="Configured model"
        onChange={(event) => setModelOverride(event.target.value)}
      />
      <a className="text-[11px]" href="/admin-login#/exposure-map" target="_blank" rel="noreferrer" title="Open the browser sign-in prompt for protected actions">
        Unlock protected actions
      </a>
      <button type="button" className="btn btn-primary btn-xs" onClick={startAnalysis} disabled={analyzing || selectedArticleId === null}>
        {analyzing ? 'Analyzing…' : payload?.has_run ? 'Use cached analysis' : 'Analyze article'}
      </button>
    </>
  )

  return (
    <main className="flex flex-wrap items-start gap-3">
      <Panel title="Recent news" meta={demoMode ? 'seeded demo' : undefined} flush className="flex-[1_1_260px]">
        {articlesLoaded && articles.length === 0 ? <p className="p-3.5 text-dim">No scored articles are available yet.</p> : null}
        <ul aria-label="Recent news" className="flex max-h-72 flex-col gap-1 overflow-y-auto p-2 lg:max-h-[calc(100dvh-var(--app-chrome)-40px)]">
          {articles.map((article) => (
            <li key={article.id}>
              <button
                type="button"
                aria-pressed={article.id === selectedArticleId}
                onClick={() => pickArticle(article.id)}
                className={`flex w-full flex-col gap-1 rounded-md border p-2.5 text-left ${article.id === selectedArticleId ? 'border-base-300 bg-raised' : 'border-transparent hover:bg-raised/60'}`}
              >
                <span className="font-mono text-[11px] text-dim">Evidence {formatEvidenceScore(article.evidence_score)}</span>
                <span className="font-medium">{article.title}</span>
                <span className="text-[11px] text-dim">{article.source || 'Unknown source'} · {formatDate(article.published_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title={selectedArticle?.title ?? 'Select an article'}
        actions={analysisControls}
        flush
        className="flex-[3_1_520px]"
      >
        {graphReady ? (
          <div className="flex flex-wrap items-end gap-3 border-b border-base-300 px-3.5 py-2">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] text-dim">Impact</span>
              <Segmented label="Impact" options={IMPACT_OPTIONS} value={directionFilter} onChange={setDirectionFilter} />
            </div>
            <Select label="Position risk" options={RISK_OPTIONS} value={riskFilter} onChange={setRiskFilter} />
            <label className="flex items-center gap-1.5 pb-1 text-xs">
              <input type="checkbox" className="checkbox checkbox-xs" checked={positionPathsOnly} onChange={(event) => setPositionPathsOnly(event.target.checked)} />
              Position paths
            </label>
            <div className="ml-auto flex gap-1.5">
              <button type="button" className="btn btn-xs" onClick={() => flowInstance?.fitView({ padding: 0.2, duration: 250, maxZoom: 1 })} disabled={!flowInstance || flowNodes.length === 0}>Fit graph</button>
              <button type="button" className="btn btn-ghost btn-xs" onClick={resetFilters}>Reset filters</button>
            </div>
            <ul aria-label="Legend" className="flex basis-full flex-wrap gap-x-3.5 gap-y-1 text-[11px] text-dim">
              {LEGEND.map((item) => (
                <li key={item.label} className="inline-flex items-center gap-1.5"><i className={`size-2 rounded-full border-2 ${item.dot}`} />{item.label}</li>
              ))}
              <li>Thicker edge = higher deterministic exposure</li>
            </ul>
          </div>
        ) : null}
        {error ? <p className="m-3.5 text-error">{error}</p> : null}
        {mapLoading ? <p className="m-3.5 text-dim">Loading evidence map…</p> : null}
        {!mapLoading && payload && !payload.has_run ? (
          <p className="m-3.5 rounded-md bg-accent-bg p-3.5 text-ink-2">Select “Analyze article” to generate and cache grounded explanations.</p>
        ) : null}
        {graphReady ? (
          // The columns fill the screen below the app chrome; React Flow needs a fixed height to draw into.
          <div className="h-[560px] lg:h-[calc(100dvh-var(--app-chrome)-150px)] [--xy-background-color:var(--color-base-200)] [--xy-controls-button-background-color-hover:var(--color-base-300)] [--xy-controls-button-background-color:var(--color-raised)] [--xy-controls-button-border-color:var(--color-base-300)] [--xy-controls-button-color:var(--color-base-content)]">
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={nodeTypes}
              colorMode="dark"
              fitView
              // A lone article node otherwise zooms to 2x and looks oversized.
              fitViewOptions={{ maxZoom: 1 }}
              minZoom={0.3}
              maxZoom={1.5}
              onInit={setFlowInstance}
              onNodeClick={(_, node) => setSelectedNode(node.data as GraphNode)}
            >
              <Background color={chartTheme.axis} gap={22} size={1} />
              <Controls showFitView={false} />
            </ReactFlow>
          </div>
        ) : null}
      </Panel>

      <Panel title="Why this matters" className="flex-[1_1_280px] overflow-y-auto lg:max-h-[calc(100dvh-var(--app-chrome))]">
        <div className="flex flex-col gap-3 leading-relaxed">
          <p className="text-xs font-semibold text-warning">No trade is executed by this feature.</p>
          {demoMode ? <p className="text-xs text-warning">Seeded fictional scenario for demos. No live news, positions, or orders are used.</p> : null}
          <h3 className="font-semibold">{selectedNode?.label ?? selectedArticle?.title ?? 'No selection'}</h3>
          {selectedNode?.reason ? <p>{selectedNode.reason}</p> : null}
          {payload?.run ? <p className="text-ink-2">{payload.run.article_summary}</p> : null}
          {selectedArticle?.source_url ? <a href={selectedArticle.source_url} target="_blank" rel="noreferrer">Open source: {selectedArticle.source || 'article'}</a> : null}
          {selectedNode?.type === 'position' ? (
            selectedNode.position_state === 'historical_live' ? (
              <DrawerBlock title="Recorded live outcome">
                <p>{formatPnl(selectedNode.outcome_pnl_pct)}{selectedNode.outcome_label ? ` · ${selectedNode.outcome_label.replaceAll('_', ' ')}` : ''}</p>
                <p className="text-ink-2">Executed {selectedNode.recorded_at ? formatDate(selectedNode.recorded_at) : 'at an unknown time'} · {selectedNode.dte ?? 'N/A'} DTE at execution</p>
                {selectedNode.learned_reason ? <p className="text-ink-2">{selectedNode.learned_reason}</p> : null}
              </DrawerBlock>
            ) : (
              <DrawerBlock title="Deterministic score">
                <p>Risk {selectedNode.risk_score ?? 0} · {selectedNode.risk_level ?? 'low'} · {selectedNode.dte ?? 'N/A'} DTE</p>
                <p className="text-ink-2">{String(selectedNode.score_inputs?.formula ?? 'Position exposure × magnitude × confidence × DTE/volatility multiplier')}</p>
              </DrawerBlock>
            )
          ) : null}
          <DrawerBlock title="Risk factors">
            {payload?.position_impacts.length ? (
              <ul className="list-disc pl-4">
                {payload.position_impacts.map((impact) => <li key={impact.option_symbol}><span className="font-mono">{impact.option_symbol}</span> · {impact.risk_level} ({impact.risk_score})</li>)}
              </ul>
            ) : <p className="text-ink-2">No current paper positions are connected to this article.</p>}
          </DrawerBlock>
          <DrawerBlock title="Learned live outcomes">
            {payload?.historical_live_impacts?.length ? (
              <ul className="list-disc pl-4">
                {payload.historical_live_impacts.map((impact) => <li key={impact.execution_id}><span className="font-mono">{impact.option_symbol}</span> · {formatPnl(impact.outcome_pnl_pct)}{impact.outcome_label ? ` · ${impact.outcome_label.replaceAll('_', ' ')}` : ''}</li>)}
              </ul>
            ) : <p className="text-ink-2">No linked non-paper execution outcomes are affected by this article.</p>}
          </DrawerBlock>
          <DrawerBlock title="Uncertainty">
            {payload?.uncertainties.length ? <ul className="list-disc pl-4">{payload.uncertainties.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="text-ink-2">No additional uncertainty was recorded.</p>}
          </DrawerBlock>
          {payload?.run ? <p className="font-mono text-[11px] text-dim">Model {payload.run.model} · {payload.run.prompt_version} · {formatDate(payload.run.created_at)}</p> : null}
        </div>
      </Panel>
    </main>
  )
}

export default ExposureMapPage
