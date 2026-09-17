"""批量装载测试夹具；保留原有表结构与每一行，不用于生产数据。"""
import pyarrow as pa


def insert_rows(con, table: str, rows) -> None:
    """用 Arrow 一次插入已构造的行，避免 executemany 的逐行事务开销。"""
    columns = [row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()]
    data = pa.Table.from_arrays(
        [pa.array(column) for column in zip(*rows, strict=True)], names=columns,
    )
    con.from_arrow(data).insert_into(table)
