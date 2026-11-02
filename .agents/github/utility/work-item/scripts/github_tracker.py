import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

RUNTIME = Path(__file__).resolve().parents[1] / "runtime" / "src"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from tj_github_work_items.records import KINDS, ItemError, read_input, render  # noqa: E402
from tj_github_work_items.request_journal import RequestJournal  # noqa: E402

REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}")
MARKER = re.compile(r"\n<!-- work-item:v1 (.+) -->\n?\Z")
COMMENT = re.compile(r"\n<!-- work-item-comment:v1 (.+) -->\n?\Z")
ISSUE_TYPES = {
    "feature": "Feature",
    "story": "Story",
    "task": "Task",
    "bug": "Bug",
    "mistake": "Task",
    "debt": "Task",
}
BOARD_STATUS = "Todo"


class APIError(ItemError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def project_token(run: Any = subprocess.run) -> str | None:
    try:
        status = run(
            ["gh", "auth", "status", "--json", "hosts"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        accounts = json.loads(status.stdout)["hosts"]["github.com"]
        eligible = [
            account
            for account in accounts
            if account.get("state") == "success"
            and "project"
            in [scope.strip() for scope in account.get("scopes", "").split(",")]
        ]
        if not eligible or any(account.get("active") for account in eligible):
            return None
        token = run(
            [
                "gh",
                "auth",
                "token",
                "--hostname",
                "github.com",
                "--user",
                eligible[0]["login"],
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        return token.stdout.strip() or None
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        return None


class GitHub:
    def __init__(self, timeout: int = 60, token: str | None = None) -> None:
        self.timeout = timeout
        self.token = token

    def request(
        self,
        method: str,
        endpoint: str,
        payload: Any = None,
        paginate: bool = False,
    ) -> Any:
        executable = shutil.which("gh")
        if executable is None:
            raise APIError(
                "GitHub CLI is unavailable; verify gh authentication and access"
            )
        path, separator, query = endpoint.partition("?")
        command = [
            executable,
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
        ]
        for key, value in parse_qsl(query, keep_blank_values=True) if separator else ():
            command.extend(("--raw-field", f"{key}={value}"))
        if payload is not None:
            command.extend(["--input", "-"])
        if paginate:
            command.extend(["--paginate", "--slurp"])
        command.append(path)
        env = {**os.environ, "GH_PROMPT_DISABLED": "1"}
        env.pop("GH_DEBUG", None)
        if self.token:
            env["GH_TOKEN"] = self.token
        try:
            result = subprocess.run(
                command,
                input=json_text(payload) if payload is not None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise APIError(
                "GitHub transport unavailable; a write outcome may be uncertain"
            ) from error
        if result.returncode:
            status = re.search(r"HTTP (\d{3})", result.stderr)
            code = int(status.group(1)) if status else None
            raise APIError(
                f"GitHub {method} failed (HTTP {code or 'unknown'}); "
                "verify gh authentication and access",
                code,
            )
        try:
            data = json.loads(result.stdout)
            if paginate:
                if not isinstance(data, list) or any(
                    not isinstance(page, list) for page in data
                ):
                    raise ValueError("invalid paginated response")
                return [item for page in data for item in page]
            return data
        except (ValueError, TypeError) as error:
            raise APIError(
                "GitHub returned an invalid response; a write outcome may be uncertain"
            ) from error


def source_primary(root: str | Path) -> Path:
    root = Path(root).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        inventory = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        if (
            top.returncode
            or inventory.returncode
            or not top.stdout.rstrip("\r\n")
            or not inventory.stdout.endswith("\0\0")
        ):
            raise ItemError("cannot resolve source primary checkout from Git worktrees")
        current = Path(top.stdout.rstrip("\r\n")).resolve()
        checkouts = []
        for block in inventory.stdout[:-2].split("\0\0"):
            fields = block.split("\0")
            if (
                not fields[0].startswith("worktree ")
                or sum(field.startswith("worktree ") for field in fields) != 1
            ):
                raise ItemError("invalid Git worktree inventory")
            location = fields[0][len("worktree ") :]
            if not location or not Path(location).is_absolute():
                raise ItemError("invalid Git worktree checkout path")
            checkouts.append(Path(location).resolve())
        if current not in checkouts or not checkouts[0].is_dir():
            raise ItemError(
                "source primary checkout is unavailable or current checkout is "
                "absent from Git worktrees"
            )
        return checkouts[0]
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ItemError(
            "cannot resolve source primary checkout from Git worktrees"
        ) from error


def source_repository(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    remote = result.stdout.strip()
    if remote.startswith("git@github.com:"):
        repo = remote[len("git@github.com:") :]
    else:
        parsed = urlparse(remote)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ItemError("origin must identify a source repository on github.com")
        repo = parsed.path.lstrip("/")
    repo = repo.removesuffix(".git")
    if result.returncode or not REPOSITORY.fullmatch(repo):
        raise ItemError("origin must identify owner/repository on github.com")
    return repo


def personal_adoption(root: Path) -> dict[str, str]:
    path = root / ".agents" / "plugins.json"
    if path.is_symlink():
        raise ItemError("personal adoption selection must be a regular repository file")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ItemError("explicit personal adoption is required") from error
    plugins = value.get("plugins") if isinstance(value, dict) else None
    marketplaces = value.get("marketplaces") if isinstance(value, dict) else None
    marketplace = (
        marketplaces.get("tj-agents") if isinstance(marketplaces, dict) else None
    )
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or not isinstance(plugins, list)
        or any(not isinstance(plugin, str) for plugin in plugins)
        or not isinstance(marketplaces, dict)
        or any(
            not isinstance(name, str) or not isinstance(details, dict)
            for name, details in marketplaces.items()
        )
        or not isinstance(marketplace, dict)
        or not isinstance(marketplace.get("repository"), str)
        or value["schema_version"] != 1
        or "personal@tj-agents" not in plugins
        or marketplace["repository"] != "tj-agents/marketplace"
    ):
        raise ItemError("explicit personal adoption is required")
    return {"root": str(root), "provider": "github:work-item"}


def selection(root: str | Path) -> dict[str, Any]:
    root = source_primary(root)
    declared = root / ".problem-records.json"
    if declared.is_symlink():
        raise ItemError("problem-record declaration must be a regular repository file")
    if declared.exists():
        try:
            value = json.loads(declared.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ItemError(
                "the repository's problem-record declaration is unreadable"
            ) from error
        if value != {"schema_version": 1, "provider": "github:work-item"}:
            raise ItemError("the repository declares another problem-record provider")
    else:
        personal_adoption(root)
    return {
        "provider": "github:work-item",
        "repository": source_repository(root),
        "issue_types": dict(ISSUE_TYPES),
    }


def request_id(value: str) -> str:
    if not REQUEST.fullmatch(value):
        raise ItemError(
            "request-id must contain 8-128 letters, digits, underscores or hyphens"
        )
    return value


def issue_number(value: str, repository: str) -> int:
    prefix = f"https://github.com/{repository}/issues/".lower()
    identity = (repository + "#").lower()
    folded = value.lower() if value.isascii() else ""
    number = (
        folded[len(prefix) :]
        if folded.startswith(prefix)
        else folded[len(identity) :]
        if folded.startswith(identity)
        else ""
    )
    if not number.isascii() or not number.isdecimal() or int(number) <= 0:
        raise ItemError(
            "item must be a full issue URL or repository#number matching the "
            "selected repository"
        )
    return int(number)


def metadata(body: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(body, str):
        raise ItemError("managed issue body must be text")
    match = MARKER.search(body)
    if not match:
        raise ItemError("issue is not a managed GitHub work item")
    try:
        value = json.loads(match.group(1))
        if not isinstance(value, dict) or set(value) not in (
            {"request_id", "kind", "creation_hash", "render_hash"},
            {"request_id", "kind", "creation_hash", "render_hash", "kind_hash"},
        ):
            raise ValueError("metadata shape")
        if value["kind"] not in KINDS or not isinstance(value["request_id"], str):
            raise ValueError("metadata kind/request")
        request_id(value["request_id"])
        if any(
            not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
            for k in (
                "creation_hash",
                "render_hash",
                *(["kind_hash"] if "kind_hash" in value else []),
            )
        ):
            raise ValueError("metadata digest")
        return value, body[: match.start()]
    except (ValueError, TypeError) as error:
        raise ItemError("invalid managed issue metadata") from error


def body_for(
    item: dict[str, Any], request: str, existing: dict[str, Any] | None = None
) -> str:
    text = render(item).rstrip("\n")
    kind = item.get("kind")
    if not isinstance(kind, str):
        raise ItemError("work item kind must be text")
    info = {
        "request_id": request,
        "kind": kind,
        "creation_hash": existing["creation_hash"]
        if existing
        else digest(json_text(item)),
        "render_hash": digest(text),
        "kind_hash": digest(kind + "\n" + text),
    }
    body = text + "\n<!-- work-item:v1 " + json_text(info) + " -->\n"
    if len(body) > 65536:
        raise ItemError("rendered issue body exceeds 65536 characters")
    return body


FIELDS = (
    "fields(first:50){nodes{... on ProjectV2SingleSelectField{"
    "id name options{id name}}}}"
)
BOARDS = (
    "query($owner:String!){repositoryOwner(login:$owner){... on "
    'ProjectV2Owner{id projectsV2(first:100,query:"is:open"){'
    "pageInfo{hasNextPage} nodes{id title closed " + FIELDS + "}}}}}"
)
REPOSITORY_ID = (
    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){id}}"
)
CREATE_BOARD = (
    "mutation($owner:ID!,$title:String!,$repository:ID!){"
    "createProjectV2(input:{ownerId:$owner,title:$title,"
    "repositoryId:$repository}){projectV2{id title " + FIELDS + "}}}"
)
KIND_COLORS = {
    "bug": "RED",
    "mistake": "ORANGE",
    "debt": "YELLOW",
    "feature": "GREEN",
    "task": "BLUE",
    "story": "PURPLE",
}
KIND_FIELD = (
    "mutation($project:ID!){createProjectV2Field(input:{projectId:$project,"
    'dataType:SINGLE_SELECT,name:"Kind",singleSelectOptions:['
    + ",".join(
        '{{name:"{}",color:{},description:""}}'.format(
            kind, KIND_COLORS.get(kind, "GRAY")
        )
        for kind in KINDS
    )
    + "]}){projectV2Field{... on ProjectV2SingleSelectField{"
    "id name options{id name}}}}}"
)
ADD_ITEM = (
    "mutation($project:ID!,$content:ID!){addProjectV2ItemById("
    "input:{projectId:$project,contentId:$content}){item{id status:"
    'fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{'
    "name}}}}}"
)
SET_OPTION = (
    "mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){"
    "updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,"
    "fieldId:$field,value:{singleSelectOptionId:$option}}){projectV2Item{"
    'id status:fieldValueByName(name:"Status"){... on '
    "ProjectV2ItemFieldSingleSelectValue{name}} kind:"
    'fieldValueByName(name:"Kind"){... on '
    "ProjectV2ItemFieldSingleSelectValue{name}}}}}"
)


class Tracker:
    def __init__(
        self,
        config: dict[str, Any],
        api: Any = None,
        journal: RequestJournal | None = None,
        board_api: Any = None,
    ) -> None:
        self.config = config
        self.repo = config["repository"]
        self.api = api or GitHub()
        self.board_api = board_api or self.api
        self.journal = journal
        self.endpoint = "repos/" + self.repo + "/issues"

    def all(self) -> list[dict[str, Any]]:
        return [
            issue
            for issue in self.api.request(
                "GET", self.endpoint + "?state=all&per_page=100", paginate=True
            )
            if "pull_request" not in issue
        ]

    def get(self, number: int) -> dict[str, Any]:
        issue = self.api.request("GET", self.endpoint + f"/{number}")
        if (
            not isinstance(issue, dict)
            or issue.get("number") != number
            or "pull_request" in issue
        ):
            raise ItemError("target is not the expected issue")
        metadata(issue.get("body"))
        return issue

    def compact(self, issue: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(issue, dict)
            or type(issue.get("number")) is not int
            or issue["number"] <= 0
        ):
            raise ItemError("invalid issue response identity")
        if (
            str(issue.get("html_url")).casefold()
            != f"https://github.com/{self.repo}/issues/{issue['number']}".casefold()
            or issue.get("state") not in ("open", "closed")
            or not isinstance(issue.get("title"), str)
        ):
            raise ItemError("invalid issue response fields")
        info, _ = metadata(issue.get("body"))
        return {
            "identity": f"{self.repo}#{issue['number']}",
            "url": issue["html_url"],
            "state": issue["state"],
            "title": issue["title"],
            "kind": info["kind"],
            "body_hash": digest(issue["body"]),
        }

    def find_request(self, request: str, item: dict[str, Any]) -> dict[str, Any] | None:
        found = []
        for issue in self.all():
            try:
                info, _ = metadata(issue.get("body"))
            except ItemError:
                continue
            if info["request_id"] == request:
                if info["creation_hash"] != digest(json_text(item)):
                    raise ItemError(
                        "request-id already exists with different creation facts"
                    )
                found.append(issue)
        if len(found) > 1:
            raise ItemError(
                "request-id has multiple issues; reconcile their ownership "
                "before continuing"
            )
        return found[0] if found else None

    def create(
        self, item: dict[str, Any], request: str, reconcile_only: bool = False
    ) -> dict[str, Any]:
        request_id(request)
        body = body_for(item, request)
        key = self.repo + ":create:" + request
        fingerprint = digest(json_text(item))
        receipt = self.journal.read(key, fingerprint) if self.journal else None
        existing = self.find_request(request, item)
        if existing is None and receipt and receipt["resource_id"]:
            resource_id = receipt["resource_id"]
            if not isinstance(resource_id, int):
                raise ItemError("request receipt has no durable issue identity")
            existing = self.get(resource_id)
            info, _ = metadata(existing["body"])
            if info["request_id"] != request or info["creation_hash"] != fingerprint:
                raise ItemError("returned issue no longer matches its request receipt")
        if existing:
            if self.journal:
                self.journal.complete(key, fingerprint, existing["number"])
            return {
                **self.compact(existing),
                "reconciled": True,
                "boards": self.place(existing, item["kind"]),
            }
        if receipt or reconcile_only:
            raise ItemError(
                "request outcome remains uncertain; reconcile without another POST"
            )
        payload = {"title": item["title"], "body": body}
        issue_type = self.config["issue_types"].get(item["kind"])
        if issue_type:
            try:
                available = self.api.request(
                    "GET", "repos/" + self.repo + "/issue-types"
                )
            except APIError as error:
                if error.status != 404:
                    raise
                available = []
            names = (
                [entry.get("name") for entry in available if isinstance(entry, dict)]
                if isinstance(available, list)
                else []
            )
            if issue_type in names:
                payload["type"] = issue_type
        if self.journal:
            self.journal.reserve(key, fingerprint)
        try:
            issue = self.api.request("POST", self.endpoint, payload)
            result = self.compact(issue)
            if self.journal:
                self.journal.complete(key, fingerprint, issue["number"])
        except (APIError, ItemError, KeyError, TypeError) as error:
            existing = self.find_request(request, item)
            if existing:
                if self.journal:
                    self.journal.complete(key, fingerprint, existing["number"])
                return {
                    **self.compact(existing),
                    "reconciled": True,
                    "boards": self.place(existing, item["kind"]),
                }
            raise ItemError(
                "create outcome uncertain or rejected; retain request-id and "
                "reconcile before retrying"
            ) from error
        return {
            **result,
            "reconciled": False,
            "boards": self.place(issue, item["kind"]),
        }

    def graphql(self, query: str, **variables: Any) -> dict[str, Any]:
        response = self.board_api.request(
            "POST", "graphql", {"query": query, "variables": variables}
        )
        if (
            not isinstance(response, dict)
            or response.get("errors")
            or not isinstance(response.get("data"), dict)
        ):
            raise APIError(
                "GitHub GraphQL request failed; verify gh has the project scope"
            )
        data = response["data"]
        if not isinstance(data, dict):
            raise APIError(
                "GitHub GraphQL request failed; verify gh has the project scope"
            )
        return data

    def boards(self, owner: str) -> tuple[str, dict[str, dict[str, Any]]]:
        data = self.graphql(BOARDS, owner=owner)["repositoryOwner"]
        if data["projectsV2"]["pageInfo"]["hasNextPage"]:
            raise APIError(
                "the owner has more than 100 open projects; place the item manually"
            )
        found: dict[str, dict[str, Any]] = {}
        for node in data["projectsV2"]["nodes"]:
            if not node["closed"]:
                found.setdefault(node["title"], node)
        return data["id"], found

    def board(
        self,
        owner_id: str,
        found: dict[str, dict[str, Any]],
        title: str,
        owner: str,
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        project = found.get(title)
        if project is None:
            repository = self.graphql(
                REPOSITORY_ID, owner=owner, name=self.repo.split("/")[1]
            )["repository"]["id"]
            project = self.graphql(
                CREATE_BOARD, owner=owner_id, title=title, repository=repository
            )["createProjectV2"]["projectV2"]
        fields = {field["name"]: field for field in project["fields"]["nodes"] if field}
        if "Kind" not in fields:
            created = self.graphql(KIND_FIELD, project=project["id"])[
                "createProjectV2Field"
            ]["projectV2Field"]
            fields["Kind"] = created
        return project["id"], fields

    def option(self, fields: dict[str, dict[str, Any]], field: str, value: str) -> str:
        details = fields.get(field)
        if not isinstance(details, dict):
            raise ItemError(f"board is missing its {field} field")
        options = details.get("options", [])
        option = next(
            (entry.get("id") for entry in options if entry.get("name") == value),
            None,
        )
        if not isinstance(option, str) or not option:
            names = ", ".join(
                entry["name"]
                for entry in options
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            )
            raise ItemError(
                f"board {field} field has no {value!r} option; available: "
                f"{names or '(none)'}"
            )
        return option

    def set_option(
        self,
        project: str,
        entry: dict[str, Any],
        fields: dict[str, dict[str, Any]],
        field: str,
        value: str,
        expected_status: str | None = None,
        expected_kind: str | None = None,
    ) -> None:
        option = self.option(fields, field, value)
        updated = self.graphql(
            SET_OPTION,
            project=project,
            item=entry["id"],
            field=fields[field]["id"],
            option=option,
        )["updateProjectV2ItemFieldValue"]["projectV2Item"]
        if expected_status is not None:
            actual = updated.get("status")
            if not isinstance(actual, dict) or actual.get("name") != expected_status:
                raise ItemError(f"board did not record Status {expected_status!r}")
        if expected_kind is not None:
            actual = updated.get("kind")
            if not isinstance(actual, dict) or actual.get("name") != expected_kind:
                raise ItemError(f"board did not record Kind {expected_kind!r}")

    def place(
        self,
        issue: dict[str, Any],
        kind: str,
        status: str = BOARD_STATUS,
        preserve_existing: bool = True,
    ) -> dict[str, Any]:
        owner, name = self.repo.split("/")
        placed = []
        try:
            owner_id, found = self.boards(owner)
            for title in dict.fromkeys((name, owner)):
                project, fields = self.board(owner_id, found, title, owner)
                entry = self.graphql(
                    ADD_ITEM, project=project, content=issue["node_id"]
                )["addProjectV2ItemById"]["item"]
                existing_status = entry.get("status")
                current_status = (
                    existing_status.get("name")
                    if preserve_existing
                    and isinstance(existing_status, dict)
                    and isinstance(existing_status.get("name"), str)
                    and existing_status["name"]
                    else None
                )
                desired_status = current_status or status
                self.option(fields, "Status", desired_status)
                self.option(fields, "Kind", kind)
                if current_status is None:
                    self.set_option(
                        project,
                        entry,
                        fields,
                        "Status",
                        desired_status,
                        desired_status,
                    )
                self.set_option(
                    project,
                    entry,
                    fields,
                    "Kind",
                    kind,
                    desired_status,
                    kind,
                )
                placed.append(title)
        except (APIError, ItemError, KeyError, TypeError) as error:
            return {
                "placed": placed,
                "error": "board placement incomplete: " + str(error),
            }
        return {"placed": placed}

    def progress(
        self,
        issue: dict[str, Any],
        status: str,
        text: str,
        request: str,
        reconcile_only: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(status, str) or not status.strip():
            raise ItemError("board status must be nonempty")
        if issue["state"] == "open" and status == "Done":
            raise ItemError("Done requires a closed issue")
        self.comment(issue["number"], text, request, "progress", reconcile_only, status)
        boards = self.place(issue, metadata(issue["body"])[0]["kind"], status, False)
        if "error" in boards:
            raise ItemError(boards["error"])
        return {**self.compact(issue), "status": status, "boards": boards}

    def update(
        self, number: int, item: dict[str, Any], expected_hash: str
    ) -> dict[str, Any]:
        issue = self.get(number)
        info, old_text = metadata(issue["body"])
        if (
            digest(issue["body"]) != expected_hash
            or digest(old_text) != info["render_hash"]
        ):
            raise ItemError(
                "issue changed; read and reconcile current content before updating"
            )
        if item["kind"] != info["kind"]:
            raise ItemError("update cannot change the work-item kind")
        payload = {
            "title": item["title"],
            "body": body_for(item, info["request_id"], info),
        }
        try:
            return self.compact(
                self.api.request("PATCH", self.endpoint + f"/{number}", payload)
            )
        except APIError as error:
            raise ItemError(
                "update outcome uncertain; read the issue before another update"
            ) from error

    def comment(
        self,
        number: int,
        text: str,
        request: str,
        operation: str = "note",
        reconcile_only: bool = False,
        status: str | None = None,
    ) -> dict[str, Any]:
        request_id(request)
        if not isinstance(text, str) or not text.strip():
            raise ItemError("comment/verification text must be nonempty")
        info = {
            "request_id": request,
            "operation": operation,
            "content_hash": digest(text),
        }
        if status is not None:
            info["status"] = status
        body = text + "\n<!-- work-item-comment:v1 " + json_text(info) + " -->\n"
        if len(body) > 65536:
            raise ItemError("comment exceeds 65536 characters")
        endpoint = self.endpoint + f"/{number}/comments"
        key = self.repo + f":comment:{number}:" + request
        fingerprint = digest(json_text(info))
        receipt = self.journal.read(key, fingerprint) if self.journal else None

        def reconcile() -> dict[str, Any] | None:
            found: list[dict[str, Any]] = []
            for comment in self.api.request(
                "GET", endpoint + "?per_page=100", paginate=True
            ):
                match = COMMENT.search(comment.get("body") or "")
                if not match:
                    continue
                try:
                    previous = json.loads(match.group(1))
                except ValueError:
                    continue
                if isinstance(previous, dict) and previous.get("request_id") == request:
                    if (
                        previous != info
                        or digest(comment["body"][: match.start()])
                        != info["content_hash"]
                    ):
                        raise ItemError(
                            "comment request-id exists with different facts"
                        )
                    found.append(comment)
            if len(found) > 1:
                raise ItemError(
                    "comment request-id has duplicates; reconcile before continuing"
                )
            if not found and receipt and receipt["resource_id"]:
                comment = self.api.request(
                    "GET",
                    "repos/"
                    + self.repo
                    + "/issues/comments/"
                    + str(receipt["resource_id"]),
                )
                if not isinstance(comment, dict) or comment.get("body") != body:
                    raise ItemError("comment changed since its request receipt")
                found.append(comment)
            return found[0] if found else None

        existing = reconcile()
        if existing:
            if self.journal:
                self.journal.complete(key, fingerprint, existing["id"])
            return existing
        if receipt or reconcile_only:
            raise ItemError(
                "comment outcome remains uncertain; reconcile without another POST"
            )
        if self.journal:
            self.journal.reserve(key, fingerprint)
        try:
            comment = self.api.request("POST", endpoint, {"body": body})
            if not isinstance(comment, dict):
                raise APIError("GitHub comment response is invalid")
            comment_id = comment.get("id")
            if (
                isinstance(comment_id, bool)
                or not isinstance(comment_id, int)
                or comment_id <= 0
            ):
                raise APIError("GitHub comment response has no valid identity")
        except APIError as error:
            existing = reconcile()
            if existing:
                if self.journal:
                    self.journal.complete(key, fingerprint, existing["id"])
                return existing
            raise ItemError(
                "comment outcome uncertain or rejected; retain request-id and "
                "reconcile before retrying"
            ) from error
        try:
            confirmed = self.api.request(
                "GET", "repos/" + self.repo + "/issues/comments/" + str(comment_id)
            )
        except APIError as error:
            raise ItemError(
                "comment outcome uncertain; reconcile before retrying"
            ) from error
        if (
            not isinstance(confirmed, dict)
            or confirmed.get("id") != comment_id
            or confirmed.get("body") != body
        ):
            raise ItemError(
                "comment changed since successful POST; reconcile before retrying"
            )
        if self.journal:
            self.journal.complete(key, fingerprint, comment_id)
        return confirmed


def add_commands(commands: Any, names: tuple[str, ...] | None = None) -> None:
    names = names or (
        "create",
        "read",
        "list",
        "state",
        "update",
        "note",
        "link",
        "progress",
        "close",
    )
    for name in names:
        command = commands.add_parser(name)
        command.add_argument("--root", default=".")
        if name in ("create", "update"):
            command.add_argument("--input", required=True)
        if name not in ("create", "list"):
            command.add_argument("--item", required=True)
        if name in ("create", "note", "link", "progress", "close"):
            command.add_argument("--request-id", required=True)
        if name in ("create", "note", "link", "progress", "close"):
            command.add_argument("--reconcile-only", action="store_true")
        if name == "update":
            command.add_argument("--expected-hash", required=True)
        if name == "list":
            command.add_argument("--kind", choices=KINDS)
            command.add_argument(
                "--state", choices=("open", "closed", "all"), default="open"
            )
        if name == "read":
            command.add_argument("--body", action="store_true")
            command.add_argument("--comments", action="store_true")
        if name in ("note", "progress", "close"):
            command.add_argument("--text-file", required=True)
        if name == "progress":
            command.add_argument("--status", required=True)
        if name == "link":
            command.add_argument("--target", required=True)


def execute(args: Any) -> dict[str, Any]:
    api = GitHub()
    board_api = (
        GitHub(token=project_token())
        if args.command in ("create", "progress", "close")
        else None
    )
    tracker = Tracker(selection(args.root), api, RequestJournal(args.root), board_api)
    if args.command == "create":
        return tracker.create(
            read_input(args.input), args.request_id, args.reconcile_only
        )
    if args.command == "list":
        items: list[dict[str, Any]] = []
        for issue in tracker.all():
            try:
                item = tracker.compact(issue)
            except ItemError:
                continue
            if (not args.kind or item["kind"] == args.kind) and (
                args.state == "all" or item["state"] == args.state
            ):
                items.append(item)
        return {"repository": tracker.repo, "items": items}
    number = issue_number(args.item, tracker.repo)
    issue = tracker.get(number)
    if args.command == "read":
        result = tracker.compact(issue)
        if args.body:
            result["body"] = issue["body"]
        if args.comments:
            result["comments"] = api.request(
                "GET",
                tracker.endpoint + f"/{number}/comments?per_page=100",
                paginate=True,
            )
        return result
    if args.command == "state":
        return tracker.compact(issue)
    if args.command == "update":
        return tracker.update(number, read_input(args.input), args.expected_hash)
    text = (
        "Linked work: " + args.target
        if args.command == "link"
        else Path(args.text_file).read_text(encoding="utf-8-sig")
    )
    if args.command == "link" and not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(issues|pull)/[1-9][0-9]*",
        args.target,
    ):
        raise ItemError("link target must be a GitHub issue or pull request URL")
    if args.command == "progress":
        return tracker.progress(
            issue, args.status, text, args.request_id, args.reconcile_only
        )
    comment = tracker.comment(
        number,
        text,
        args.request_id,
        args.command,
        getattr(args, "reconcile_only", False),
    )
    if args.command == "close":
        if issue["state"] == "open":
            try:
                api.request(
                    "PATCH",
                    tracker.endpoint + f"/{number}",
                    {"state": "closed", "state_reason": "completed"},
                )
            except APIError as error:
                raise ItemError(
                    "close outcome uncertain; read state before retrying with "
                    "the same request-id"
                ) from error
        closed = tracker.get(number)
        if closed["state"] != "closed":
            raise ItemError(
                "close outcome uncertain; read state before retrying with the "
                "same request-id"
            )
        boards = tracker.place(
            closed, metadata(closed["body"])[0]["kind"], "Done", False
        )
        if "error" in boards:
            raise ItemError(boards["error"])
        return {
            **tracker.compact(closed),
            "already_closed": issue["state"] == "closed",
            "boards": boards,
        }
    return {"identity": f"{tracker.repo}#{number}", "comment_url": comment["html_url"]}
