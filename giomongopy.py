import argparse
import sys
from datetime import datetime, timezone

try:
    from pymongo import ASCENDING, DESCENDING, MongoClient
except ImportError:
    print("Missing dependency: pymongo")
    print("Install with: pip install pymongo")
    sys.exit(1)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_seed_documents() -> dict:
    return {
        "qc_reports": [
            {
                "_id": "QCR-2026-1001",
                "orderId": "PO-2026-0001",
                "partId": "PART-A320-FP-01",
                "supplierId": "SUP-001",
                "reportType": "COMBINED",
                "status": "SUBMITTED",
                "versions": [
                    {
                        "versionNo": 1,
                        "inspectorEmpId": "E2001",
                        "recordedAt": datetime(2026, 3, 3, 12, 5, tzinfo=timezone.utc),
                        "result": "PASS",
                        "findings": {
                            "dimensional": {"status": "PASS", "deviations": []},
                            "ndt": {"status": "PASS", "method": "UT"},
                        },
                        "failureCause": None,
                        "signatureRef": "https://secure-sign.example/aeronetb/reports/QCR-2026-1001-v1.sig",
                    }
                ],
                "audit": {
                    "createdBy": "E2001",
                    "createdAt": datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc),
                },
            }
        ],
        "component_certifications": [
            {
                "_id": "CERT-2026-9001",
                "reportId": "QCR-2026-1001",
                "partId": "PART-A320-FP-01",
                "supplierId": "SUP-001",
                "inspectorEmpId": "E2001",
                "status": "APPROVED",
                "immutable": True,
                "approvedFinalizedAt": datetime(2026, 3, 3, 13, 20, tzinfo=timezone.utc),
                "materialTraceability": [
                    {
                        "batchNo": "BATCH-TI-33881",
                        "originCountry": "Germany",
                        "millCertificateRef": "mill://certs/ti33881.pdf",
                    }
                ],
                "signatures": [
                    {
                        "signerEmpId": "E2001",
                        "signatureHash": "sha256:sample-signature-hash",
                        "stampRef": "stamp://quality/E2001",
                        "signedAt": datetime(2026, 3, 3, 13, 15, tzinfo=timezone.utc),
                    }
                ],
                "payload": {
                    "testResults": {"dimensional": "PASS", "ndt": "PASS"},
                    "regulatoryRefs": ["EASA-CS-25"],
                },
            }
        ],
        "shipment_tracking": [
            {
                "_id": "SHIP-0001",
                "orderId": "PO-2026-0001",
                "trackingNo": "TRK-AX-445577",
                "carrier": "BlueSky Logistics",
                "status": "IN_TRANSIT",
                "eta": datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
                "checkpoints": [
                    {
                        "time": datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc),
                        "geo": {"lat": 37.983810, "lon": 23.727539},
                        "location": "Athens Hub",
                        "note": "Departed origin warehouse",
                    },
                    {
                        "time": datetime(2026, 3, 3, 9, 30, tzinfo=timezone.utc),
                        "geo": {"lat": 38.423733, "lon": 27.142826},
                        "location": "Aegean Sea Corridor",
                        "note": "On schedule",
                    },
                ],
                "conditions": [
                    {
                        "time": datetime(2026, 3, 3, 9, 30, tzinfo=timezone.utc),
                        "temperature": 18.4,
                        "vibration": 1.12,
                        "pressure": 101.2,
                        "shockDetected": False,
                    }
                ],
            }
        ],
        "iot_events": [
            {
                "_id": "evt-temp-20260303T100000Z",
                "deviceId": "DEV-TEMP-01",
                "targetType": "EQUIPMENT",
                "targetId": "EQ-CNC-01",
                "sensorType": "TEMPERATURE",
                "observedAt": datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc),
                "value": 52.3,
                "unit": "C",
                "threshold": {"min": 5, "max": 70},
                "isAnomaly": False,
                "rawPayload": {"temp": 52.3, "unit": "C"},
            },
            {
                "_id": "evt-gps-20260303T100000Z",
                "deviceId": "DEV-GPS-01",
                "targetType": "SHIPMENT",
                "targetId": "SHIP-0001",
                "sensorType": "GPS",
                "observedAt": datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc),
                "value": None,
                "unit": "deg",
                "geo": {"lat": 38.423733, "lon": 27.142826},
                "isAnomaly": False,
                "rawPayload": {"lat": 38.423733, "lon": 27.142826},
            },
        ],
        "audit_logs": [
            {
                "_id": "AUD-20260303-0001",
                "eventTime": now_utc(),
                "empId": "E1001",
                "action": "INSERT",
                "entity": "purchase_order",
                "entityKey": "PO-2026-0001",
                "outcome": "SUCCESS",
                "sourceIp": "10.0.0.25",
            },
            {
                "_id": "AUD-20260303-0002",
                "eventTime": now_utc(),
                "empId": "E2001",
                "action": "APPROVE",
                "entity": "component_certification",
                "entityKey": "CERT-2026-9001",
                "outcome": "SUCCESS",
                "sourceIp": "10.0.0.31",
            },
        ],
    }


def create_indexes(db) -> None:
    db.qc_reports.create_index([("supplierId", ASCENDING), ("status", ASCENDING)])
    db.qc_reports.create_index([("versions.recordedAt", DESCENDING)])

    db.component_certifications.create_index([("reportId", ASCENDING)], unique=True)
    db.component_certifications.create_index([("supplierId", ASCENDING), ("status", ASCENDING)])

    db.shipment_tracking.create_index([("trackingNo", ASCENDING)], unique=True)
    db.shipment_tracking.create_index([("status", ASCENDING), ("eta", ASCENDING)])
    db.shipment_tracking.create_index([("checkpoints.geo", "2dsphere")])

    db.iot_events.create_index([("deviceId", ASCENDING), ("observedAt", DESCENDING)])
    db.iot_events.create_index([("targetType", ASCENDING), ("targetId", ASCENDING), ("observedAt", DESCENDING)])

    db.audit_logs.create_index([("eventTime", DESCENDING)])
    db.audit_logs.create_index([("empId", ASCENDING), ("eventTime", DESCENDING)])


def seed_database(uri: str, database_name: str, reset: bool) -> None:
    client = MongoClient(uri, serverSelectionTimeoutMS=7000)
    db = client[database_name]

    client.admin.command("ping")
    print(f"Connected to MongoDB. Database: {database_name}")

    seed_docs = build_seed_documents()

    if reset:
        for collection_name in seed_docs.keys():
            db[collection_name].drop()
        print("Dropped existing target collections.")

    for collection_name, docs in seed_docs.items():
        collection = db[collection_name]
        if docs:
            collection.insert_many(docs, ordered=True)
            print(f"Inserted {len(docs)} docs into '{collection_name}'.")

    create_indexes(db)
    print("Indexes created successfully.")

    print("\nCollection counts:")
    for collection_name in seed_docs.keys():
        print(f"- {collection_name}: {db[collection_name].count_documents({})}")



def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AeroNetB MongoDB collections.")
    parser.add_argument("--uri", default="mongodb://localhost:27017", help="MongoDB connection URI")
    parser.add_argument("--db", default="aeronetb", help="MongoDB database name")
    parser.add_argument("--reset", action="store_true", help="Drop collections before inserting seed data")

    args = parser.parse_args()

    try:
        seed_database(uri=args.uri, database_name=args.db, reset=args.reset)
        print("MongoDB seed completed.")
    except Exception as exc:
        print(f"MongoDB setup failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
