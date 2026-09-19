import type { ReactNode } from "react";

export interface Column<T> {
  /** Stable identity for React keys; also used as the header test handle. */
  key: string;
  header: string;
  /** Right-align and use tabular figures. */
  numeric?: boolean;
  render: (row: T) => ReactNode;
}

export interface DataTableProps<T> {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  caption?: string;
}

/**
 * A small accessible table (MVP 5 / SP 5.10, SP 5.12).
 *
 * Sticky headers and a horizontal scroll container keep long identifiers
 * readable; the caption is what makes the table meaningful to a screen reader.
 */
export function DataTable<T>({ columns, rows, rowKey, caption }: DataTableProps<T>) {
  return (
    <div className="table-scroll">
      <table className="table">
        {caption !== undefined ? <caption>{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={column.numeric === true ? "table__numeric" : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={column.numeric === true ? "table__numeric" : undefined}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
