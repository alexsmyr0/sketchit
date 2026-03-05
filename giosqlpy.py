import argparse
import os
import re
import sys
from typing import List

try:
    import pyodbc
except ImportError:
    print("Missing dependency: pyodbc")
    print("Install with: pip install pyodbc")
    sys.exit(1)


def split_sql_batches(sql_text: str) -> List[str]:
    parts = re.split(r"(?im)^\s*GO\s*(?:--.*)?$", sql_text)
    return [p.strip() for p in parts if p.strip()]


def get_sql_server_drivers() -> List[str]:
    preferred = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server",
    ]
    installed = list(pyodbc.drivers())
    available = [driver for driver in preferred if driver in installed]
    return available


def execute_sql_file(connection_string: str, sql_file: str) -> None:
    if not os.path.exists(sql_file):
        raise FileNotFoundError(f"SQL file not found: {sql_file}")

    with open(sql_file, "r", encoding="utf-8") as file:
        sql_text = file.read()

    batches = split_sql_batches(sql_text)
    if not batches:
        print("No SQL batches found to execute.")
        return

    print(f"Connecting to SQL Server...")
    with pyodbc.connect(connection_string, autocommit=False) as connection:
        cursor = connection.cursor()
        try:
            for index, batch in enumerate(batches, start=1):
                print(f"Executing batch {index}/{len(batches)}")
                cursor.execute(batch)
            connection.commit()
            print("SQL script executed successfully.")
        except Exception as exc:
            connection.rollback()
            print(f"Execution failed. Transaction rolled back. Error: {exc}")
            raise


def build_connection_string(server: str, database: str, username: str | None, password: str | None, trusted: bool, driver: str) -> str:
    if trusted:
        return (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            "Trusted_Connection=yes;"
        )

    if not username or not password:
        raise ValueError("username and password are required when --trusted is not used")

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
    )


def is_driver_error(error: Exception) -> bool:
    return "IM002" in str(error)


def is_connection_error(error: Exception) -> bool:
    text = str(error)
    return "08001" in text or "DBNETLIB" in text or "ConnectionOpen" in text


def main() -> None:
    parser = argparse.ArgumentParser(description="Run gio.sql against SQL Server.")
    parser.add_argument("--server", default="localhost", help="SQL Server host or host\\instance")
    parser.add_argument("--database", default="master", help="Target database name")
    parser.add_argument("--sql", default="gio.sql", help="Path to SQL file")
    parser.add_argument("--driver", default=None, help="ODBC driver name (optional; auto-detect when omitted)")
    parser.add_argument("--trusted", action="store_true", help="Use Windows integrated authentication")
    parser.add_argument("--username", default=None, help="SQL username")
    parser.add_argument("--password", default=None, help="SQL password")
    parser.add_argument("--list-drivers", action="store_true", help="Print installed SQL Server ODBC drivers and exit")

    args = parser.parse_args()

    detected_drivers = get_sql_server_drivers()

    if args.list_drivers:
        if detected_drivers:
            print("Installed SQL Server ODBC drivers:")
            for driver in detected_drivers:
                print(f"- {driver}")
        else:
            print("No SQL Server ODBC driver found.")
        return

    drivers_to_try: List[str] = [args.driver] if args.driver else detected_drivers

    if not drivers_to_try:
        print("No SQL Server ODBC driver found.")
        print("Install one of: ODBC Driver 18 for SQL Server, ODBC Driver 17 for SQL Server")
        sys.exit(1)

    last_error: Exception | None = None

    for driver in drivers_to_try:
        try:
            print(f"Trying driver: {driver}")
            conn_str = build_connection_string(
                server=args.server,
                database=args.database,
                username=args.username,
                password=args.password,
                trusted=args.trusted,
                driver=driver,
            )
            execute_sql_file(conn_str, args.sql)
            return
        except Exception as error:
            last_error = error
            print(f"Driver failed: {driver}")
            print(f"Reason: {error}")
            if args.driver:
                break

    print("\nUnable to execute SQL script.")
    if last_error and is_driver_error(last_error):
        print("Cause: ODBC driver mismatch or missing driver.")
        print("Tip: run with --list-drivers and choose one via --driver.")
    elif last_error and is_connection_error(last_error):
        print("Cause: SQL Server instance is not reachable.")
        print("Tip: ensure SQL Server is installed/running, then pass the correct --server value (e.g. HOST\\INSTANCE).")
    else:
        print("Cause: see error above.")

    sys.exit(1)


if __name__ == "__main__":
    main()
