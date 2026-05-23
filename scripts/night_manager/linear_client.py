"""Linear GraphQL client for fetching issues and posting updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("night_manager.linear")

GRAPHQL_URL = "https://api.linear.app/graphql"


@dataclass
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str | None
    priority: int
    sort_order: float
    state_name: str
    state_id: str
    labels: list[str]
    estimate: int | None
    team_id: str


class LinearClient:
    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._client.post(GRAPHQL_URL, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"Linear GraphQL errors: {body['errors']}")
        return body["data"]

    async def fetch_triaged_issues(self, label_name: str, team_key: str = "ABS") -> list[LinearIssue]:
        query = """
        query TriagedIssues($labelName: String!, $teamKey: String!) {
          issues(
            filter: {
              team: { key: { eq: $teamKey } }
              labels: { name: { eq: $labelName } }
              state: { type: { in: ["backlog", "unstarted"] } }
            }
            orderBy: updatedAt
          ) {
            nodes {
              id
              identifier
              title
              description
              priority
              sortOrder
              state { id name }
              labels { nodes { name } }
              estimate
              team { id }
            }
          }
        }
        """
        data = await self._query(query, {"labelName": label_name, "teamKey": team_key})
        nodes = data["issues"]["nodes"]
        issues = [
            LinearIssue(
                id=n["id"],
                identifier=n["identifier"],
                title=n["title"],
                description=n.get("description"),
                priority=n["priority"],
                sort_order=n["sortOrder"],
                state_name=n["state"]["name"],
                state_id=n["state"]["id"],
                labels=[lb["name"] for lb in n["labels"]["nodes"]],
                estimate=n.get("estimate"),
                team_id=n["team"]["id"],
            )
            for n in nodes
        ]
        issues.sort(key=lambda i: i.sort_order)
        return issues

    async def fetch_issue(self, identifier: str) -> LinearIssue:
        query = """
        query GetIssue($identifier: String!) {
          issue(id: $identifier) {
            id
            identifier
            title
            description
            priority
            sortOrder
            state { id name }
            labels { nodes { name } }
            estimate
            team { id }
          }
        }
        """
        data = await self._query(query, {"identifier": identifier})
        n = data["issue"]
        return LinearIssue(
            id=n["id"],
            identifier=n["identifier"],
            title=n["title"],
            description=n.get("description"),
            priority=n["priority"],
            sort_order=n["sortOrder"],
            state_name=n["state"]["name"],
            state_id=n["state"]["id"],
            labels=[lb["name"] for lb in n["labels"]["nodes"]],
            estimate=n.get("estimate"),
            team_id=n["team"]["id"],
        )

    async def get_workflow_states(self, team_key: str = "ABS") -> dict[str, str]:
        """Return {state_name: state_id} for the team."""
        query = """
        query WorkflowStates($teamKey: String!) {
          workflowStates(filter: { team: { key: { eq: $teamKey } } }) {
            nodes { id name type }
          }
        }
        """
        data = await self._query(query, {"teamKey": team_key})
        return {n["name"]: n["id"] for n in data["workflowStates"]["nodes"]}

    async def update_issue_state(self, issue_id: str, state_id: str) -> None:
        mutation = """
        mutation UpdateIssue($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) {
            success
          }
        }
        """
        await self._query(mutation, {"id": issue_id, "stateId": state_id})
        log.info("Updated issue %s to state %s", issue_id, state_id)

    async def post_comment(self, issue_id: str, body: str) -> None:
        mutation = """
        mutation PostComment($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
          }
        }
        """
        await self._query(mutation, {"issueId": issue_id, "body": body})
        log.info("Posted comment on %s", issue_id)

    async def search_issue_by_identifier(self, identifier: str) -> LinearIssue | None:
        """Search for an issue by its human-readable identifier (e.g. ABS-88)."""
        query = """
        query SearchIssue($filter: IssueFilter!) {
          issues(filter: $filter, first: 1) {
            nodes {
              id
              identifier
              title
              description
              priority
              sortOrder
              state { id name }
              labels { nodes { name } }
              estimate
              team { id }
            }
          }
        }
        """
        team_key, number = identifier.split("-", 1)
        data = await self._query(query, {
            "filter": {
                "team": {"key": {"eq": team_key}},
                "number": {"eq": int(number)},
            },
        })
        nodes = data["issues"]["nodes"]
        if not nodes:
            return None
        n = nodes[0]
        return LinearIssue(
            id=n["id"],
            identifier=n["identifier"],
            title=n["title"],
            description=n.get("description"),
            priority=n["priority"],
            sort_order=n["sortOrder"],
            state_name=n["state"]["name"],
            state_id=n["state"]["id"],
            labels=[lb["name"] for lb in n["labels"]["nodes"]],
            estimate=n.get("estimate"),
            team_id=n["team"]["id"],
        )
