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
    return <p>{error}</p>;
  }

  if (data.length === 0) {
    return <p>Loading chart...</p>;
  }

  return (
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
          <CartesianGrid stroke="#aaa" strokeDasharray="5 5" />
          <XAxis dataKey="x" />
          <YAxis width="auto" />
          <Tooltip />
          <Legend />
          <Line
            type="monotone"
            dataKey="y"
            stroke="#2563eb"
            strokeWidth={2}
            dot={false}
            name="Equity"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default Graph;
