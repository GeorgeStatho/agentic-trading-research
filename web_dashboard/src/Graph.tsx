import { useEffect, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { getGraphData } from './displayPreviousorder';
import './graph.css';

type GraphPoint = {
  x: string;
  y: number;
  rawTimestamp: string | number;
};

const GRAPH_POLL_INTERVAL_MS = 60_000;

function Graph() {
  const [data, setData] = useState<GraphPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const loadGraphData = () => {
      getGraphData()
        .then((points) => {
          if (!isMounted) {
            return;
          }

          setData(points);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!isMounted) {
            return;
          }

          setError(err instanceof Error ? err.message : 'Failed to load chart data.');
        });
    };

    loadGraphData();
    const intervalId = window.setInterval(loadGraphData, GRAPH_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  if (error) {
    return (
      <section className="graph-panel">
        <div className="graph-panel__header">
          <div><p>Equity History</p><h2>Portfolio curve unavailable</h2></div>
        </div>
        <p className="graph-panel__empty">{error}</p>
      </section>
    );
  }

  if (data.length === 0) {
    return (
      <section className="graph-panel">
        <div className="graph-panel__header">
          <div><p>Equity History</p><h2>Portfolio equity curve</h2></div>
        </div>
        <p className="graph-panel__empty">Loading chart data…</p>
      </section>
    );
  }

  return (
    <section className="graph-panel">
      <div className="graph-panel__header">
        <div>
          <p>Equity History</p>
          <h2>Portfolio equity curve</h2>
        </div>
        <span>{data.length} recorded points</span>
      </div>
      <div className="graph">
        <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{
            top: 20,
            right: 20,
            bottom: 5,
            left: 0,
          }}
        >
          <CartesianGrid stroke="#3d4658" strokeDasharray="4 5" />
          <XAxis dataKey="x" tick={{ fill: '#9ca3af', fontSize: 12 }} axisLine={{ stroke: '#3d4658' }} tickLine={false} />
          <YAxis width="auto" tick={{ fill: '#9ca3af', fontSize: 12 }} axisLine={{ stroke: '#3d4658' }} tickLine={false} />
          <Tooltip contentStyle={{ background: '#202838', border: '1px solid #3d4658', borderRadius: 10 }} labelStyle={{ color: '#f3f4f6' }} itemStyle={{ color: '#c4ccda' }} />
          <Legend />
          <Line
            type="monotone"
            dataKey="y"
            stroke="#a58cf4"
            strokeWidth={2.5}
            dot={false}
            name="Equity"
          />
        </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export default Graph;
