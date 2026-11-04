import type { ReactNode } from 'react'

export type Column<Row> = {
  key: string
  header: ReactNode
  render: (row: Row) => ReactNode
  /** Numbers go right so their digits line up. */
  align?: 'left' | 'right'
}

type DataTableProps<Row> = {
  columns: readonly Column<Row>[]
  rows: readonly Row[]
  rowKey: (row: Row) => string
  /** Extra classes per row, e.g. `opacity-60` for canceled orders. */
  rowClassName?: (row: Row) => string
  emptyMessage?: ReactNode
  /** The table's accessible name, when a page has more than one. */
  label?: string
}

function alignClass(align: Column<unknown>['align']): string {
  return align === 'right' ? 'text-right' : ''
}

/** A compact table that scrolls sideways inside its own box on narrow screens. */
export function DataTable<Row>({ columns, rows, rowKey, rowClassName, emptyMessage = 'Nothing to show', label }: DataTableProps<Row>) {
  return (
    <div className="overflow-x-auto">
      <table className="table table-xs" aria-label={label}>
        <thead>
          <tr className="bg-head">
            {columns.map((column) => (
              <th key={column.key} scope="col" className={`text-[11px] font-medium text-dim ${alignClass(column.align)}`}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={columns.length} className="py-3 text-dim">{emptyMessage}</td></tr>
          ) : rows.map((row) => (
            <tr key={rowKey(row)} className={`border-raised ${rowClassName?.(row) ?? ''}`}>
              {columns.map((column) => (
                <td key={column.key} className={alignClass(column.align)}>{column.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
