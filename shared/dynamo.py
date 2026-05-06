"""DynamoDB helper used by both server and worker.

Wraps the boto3 resource API so callers pass dicts in/out and don't deal with
the low-level type-marshaled format. Empty Python sets are dropped before
writes because DynamoDB rejects empty StringSets.
"""

import boto3
from boto3.dynamodb.conditions import Key

from .constants import (
    TABLE_CUSTOMER_AUTH,
    TABLE_CUSTOMER_CONSENT,
    TABLE_CUSTOMER_EVENTS,
    TABLE_JOBS,
)


def _strip_empty_sets(item: dict) -> dict:
    return {k: v for k, v in item.items() if not (isinstance(v, set) and not v)}


class DynamoClient:
    def __init__(self, endpoint: str, region: str = "us-east-1"):
        self.resource = boto3.resource(
            "dynamodb",
            endpoint_url=endpoint,
            region_name=region,
        )

    def table(self, name: str):
        return self.resource.Table(name)

    # --- customer_events ---------------------------------------------------

    def put_event(self, event: dict) -> None:
        item = _strip_empty_sets({
            "PK": f"CUSTOMER#{event['customer_id']}",
            "SK": f"EVENT#{event['event_id']}",
            **event,
        })
        self.table(TABLE_CUSTOMER_EVENTS).put_item(Item=item)

    def batch_put_events(self, events: list[dict]) -> None:
        """Bulk insert. boto3's batch_writer chunks at 25 and retries unprocessed items.

        SK is keyed on event_id alone, so a retry with the same client_event_id
        overwrites the existing row instead of inserting a twin.
        """
        if not events:
            return
        with self.table(TABLE_CUSTOMER_EVENTS).batch_writer() as bw:
            for event in events:
                item = _strip_empty_sets({
                    "PK": f"CUSTOMER#{event['customer_id']}",
                    "SK": f"EVENT#{event['event_id']}",
                    **event,
                })
                bw.put_item(Item=item)

    def get_event(self, customer_id: str, event_id: str) -> dict | None:
        resp = self.table(TABLE_CUSTOMER_EVENTS).get_item(
            Key={
                "PK": f"CUSTOMER#{customer_id}",
                "SK": f"EVENT#{event_id}",
            }
        )
        return resp.get("Item")

    def query_events(self, customer_id: str) -> list[dict]:
        resp = self.table(TABLE_CUSTOMER_EVENTS).query(
            KeyConditionExpression=Key("PK").eq(f"CUSTOMER#{customer_id}")
        )
        return resp.get("Items", [])

    def delete_event(self, customer_id: str, event_id: str) -> None:
        self.table(TABLE_CUSTOMER_EVENTS).delete_item(
            Key={
                "PK": f"CUSTOMER#{customer_id}",
                "SK": f"EVENT#{event_id}",
            }
        )

    def delete_all_events_for_customer(self, customer_id: str) -> int:
        """Delete every event for a customer. Returns count deleted."""
        events = self.query_events(customer_id)
        for event in events:
            self.table(TABLE_CUSTOMER_EVENTS).delete_item(
                Key={"PK": event["PK"], "SK": event["SK"]}
            )
        return len(events)

    def update_event_status(
        self,
        customer_id: str,
        event_id: str,
        status: str,
    ) -> None:
        self.table(TABLE_CUSTOMER_EVENTS).update_item(
            Key={
                "PK": f"CUSTOMER#{customer_id}",
                "SK": f"EVENT#{event_id}",
            },
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )

    # --- customer_consent --------------------------------------------------

    def put_consent(self, consent: dict) -> None:
        item = _strip_empty_sets({
            "PK": f"CUSTOMER#{consent['customer_id']}",
            "SK": "CONSENT",
            **consent,
        })
        self.table(TABLE_CUSTOMER_CONSENT).put_item(Item=item)

    def get_consent(self, customer_id: str) -> dict | None:
        resp = self.table(TABLE_CUSTOMER_CONSENT).get_item(
            Key={"PK": f"CUSTOMER#{customer_id}", "SK": "CONSENT"}
        )
        return resp.get("Item")

    def delete_consent(self, customer_id: str) -> bool:
        """Returns True if a consent record was deleted, False if not found."""
        existing = self.get_consent(customer_id)
        if not existing:
            return False
        self.table(TABLE_CUSTOMER_CONSENT).delete_item(
            Key={"PK": f"CUSTOMER#{customer_id}", "SK": "CONSENT"}
        )
        return True

    # --- jobs --------------------------------------------------------------

    def put_job(self, job: dict) -> None:
        item = _strip_empty_sets({
            "PK": f"JOB#{job['job_id']}",
            "SK": "META",
            **job,
        })
        self.table(TABLE_JOBS).put_item(Item=item)

    def batch_put_jobs(self, jobs: list[dict]) -> None:
        if not jobs:
            return
        with self.table(TABLE_JOBS).batch_writer() as bw:
            for job in jobs:
                item = _strip_empty_sets({
                    "PK": f"JOB#{job['job_id']}",
                    "SK": "META",
                    **job,
                })
                bw.put_item(Item=item)

    def get_job(self, job_id: str) -> dict | None:
        resp = self.table(TABLE_JOBS).get_item(
            Key={"PK": f"JOB#{job_id}", "SK": "META"}
        )
        return resp.get("Item")

    def update_job_status(
        self,
        job_id: str,
        status: str,
        completed_at: str | None = None,
        error: str | None = None,
    ) -> None:
        update_parts = ["#s = :s"]
        names = {"#s": "status"}
        values = {":s": status}
        if completed_at:
            update_parts.append("completed_at = :c")
            values[":c"] = completed_at
        if error:
            update_parts.append("#e = :e")
            names["#e"] = "error"
            values[":e"] = error
        self.table(TABLE_JOBS).update_item(
            Key={"PK": f"JOB#{job_id}", "SK": "META"},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    # --- customer_auth -----------------------------------------------------

    def put_auth(self, record: dict) -> None:
        """Insert a new auth row. Raises ClientError (ConditionalCheckFailed)
        if the email is already registered."""
        email_key = record["email"].lower()
        item = _strip_empty_sets({
            "PK": f"EMAIL#{email_key}",
            "SK": "AUTH",
            **record,
            "email": email_key,
        })
        self.table(TABLE_CUSTOMER_AUTH).put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get_auth_by_email(self, email: str) -> dict | None:
        resp = self.table(TABLE_CUSTOMER_AUTH).get_item(
            Key={"PK": f"EMAIL#{email.lower()}", "SK": "AUTH"}
        )
        return resp.get("Item")
