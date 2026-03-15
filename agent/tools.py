from typing import Optional, Any, Literal
from langchain_core.tools import BaseTool, tool
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger

# Import existing core logic directly to reuse it, avoiding HTTP calls
from routes import integration

logger = get_logger("agent.tools")


# ─── Tool Definitions (schema-only, no DB binding) ─────────────────────────
# These define the function signatures and docstrings the LLM sees.
# Created once at module level so bind_tools() only runs once.

@tool
async def list_projects(
    vertical: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """
    List projects with summaries.
    - `vertical`: Filter by vertical name (e.g. "knots", "pluto", "festia").
    - `search`: Searches across client name, groom name, bride name, child name,
       event name, company name, project code, AND event types (e.g. "Reception", "Haldi").
       Use this when the user mentions a person's name or event type.
    - Returns `project_name` (resolved title like "Aswin & Priya"), `metadata`, and event summaries.
    """
    pass  # Schema-only; execution handled by create_tool_executors


@tool
async def get_project_details(
    id_or_code: str,
    view: Literal["full", "team", "schedule", "deliverables"] = "full"
) -> dict[str, Any]:
    """
    Fetch details for a project by _id or code.
    Codes are auto-normalized: 'KN 2026 1234' -> 'KN-2026-1234', 'kn-2026-0001' -> 'KN-2026-0001'.
    view="full": Key project info with event summaries (not raw DB data).
    view="team": Event names, dates, and assigned associates.
    view="schedule": Event names, dates, and venues.
    view="deliverables": Incomplete deliverables.
    """
    pass


@tool
async def list_clients(
    type: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List clients. Use search to find by exact name or phone."""
    pass


@tool
async def list_associates(
    role: Optional[str] = None,
    employment_type: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """List associates (team members). Use search by name or phone."""
    pass


@tool
async def get_associate_contact(search: str) -> dict[str, Any]:
    """Fast lookup for an associate's contact info (email, phone) matching a name."""
    pass


@tool
async def get_associate_assignments(associate_id: str) -> dict[str, Any]:
    """
    Find projects & events an associate is assigned to by their exact _id.
    Use list_associates to find the _id first.
    """
    pass


@tool
async def list_events(
    vertical: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    unassigned_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Flat list of events across all projects. Dates must be ISO strings.
    - `search`: Matches event type (e.g. "Reception"), client/groom/bride/child name, company name,
      or project code. Use this for queries like "When is Aswin's reception?" with search="Aswin".
    - `from_date`/`to_date`: Filter by date range.
    - `unassigned_only`: Only events with no team assigned.
    """
    pass


@tool
async def list_verticals() -> dict[str, Any]:
    """Get active verticals and their project counts."""
    pass


@tool
async def get_statistics(
    module: Literal["dashboard", "finance", "projects", "clients", "associates"],
    vertical: Optional[str] = None
) -> dict[str, Any]:
    """
    Get aggregate stats. module="dashboard": overall system stats.
    module="finance": totals and net profit.
    module="projects": active/ongoing breakdown (can optionally filter by vertical).
    """
    pass


# ─── Public API ─────────────────────────────────────────────────────────────

# Tool definitions list — used once by bind_tools() at module level in graph.py
_TOOL_DEFS: list[BaseTool] = [
    list_projects,
    get_project_details,
    list_clients,
    list_associates,
    get_associate_contact,
    get_associate_assignments,
    list_events,
    list_verticals,
    get_statistics,
]


def get_tool_defs() -> list[BaseTool]:
    """Return tool definitions (schemas). These never change between requests."""
    return _TOOL_DEFS


def create_tool_executors(db: ScopedDatabase) -> list[BaseTool]:
    """
    Create request-scoped tool executors bound to a specific agency's database.
    These are lightweight closures — the heavy schema work is already done in _TOOL_DEFS.
    """

    @tool
    async def list_projects(
        vertical: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        List projects with summaries.
        - `vertical`: Filter by vertical name (e.g. "knots", "pluto", "festia").
        - `search`: Searches across client name, groom name, bride name, child name,
           event name, company name, project code, AND event types (e.g. "Reception", "Haldi").
           Use this when the user mentions a person's name or event type.
        - Returns `project_name` (resolved title like "Aswin & Priya"), `metadata`, and event summaries.
        """
        try:
            return await integration.list_projects(
                vertical=vertical, status=status, search=search, page=page, limit=limit, db=db
            )
        except Exception as e:
            logger.warning(f"list_projects failed: {e}", exc_info=True)
            return {"error": f"Failed to list projects: {str(e)}", "total": 0, "data": []}

    @tool
    async def get_project_details(
        id_or_code: str,
        view: Literal["full", "team", "schedule", "deliverables"] = "full"
    ) -> dict[str, Any]:
        """
        Fetch details for a project by _id or code.
        Codes are auto-normalized: 'KN 2026 1234' -> 'KN-2026-1234', 'kn-2026-0001' -> 'KN-2026-0001'.
        view="full": Key project info with event summaries (not raw DB data).
        view="team": Event names, dates, and assigned associates.
        view="schedule": Event names, dates, and venues.
        view="deliverables": Incomplete deliverables.
        """
        try:
            if view == "team":
                return await integration.get_project_team(identifier=id_or_code, db=db)
            elif view == "schedule":
                return await integration.get_project_schedule(identifier=id_or_code, db=db)
            elif view == "deliverables":
                return await integration.get_pending_deliverables(identifier=id_or_code, db=db)
            else:
                return await integration.get_project_summary(identifier=id_or_code, db=db)
        except Exception as e:
            logger.warning(f"get_project_details failed for '{id_or_code}': {e}", exc_info=True)
            return {"error": f"Could not find project '{id_or_code}': {str(e)}"}

    @tool
    async def list_clients(
        type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List clients. Use search to find by exact name or phone."""
        try:
            return await integration.list_clients(
                client_type=type, search=search, page=page, limit=limit, db=db
            )
        except Exception as e:
            logger.warning(f"list_clients failed: {e}", exc_info=True)
            return {"error": f"Failed to list clients: {str(e)}", "total": 0, "data": []}

    @tool
    async def list_associates(
        role: Optional[str] = None,
        employment_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List associates (team members). Use search by name or phone."""
        try:
            return await integration.list_associates(
                role=role,
                employment_type=employment_type,
                search=search,
                page=page,
                limit=limit,
                db=db,
            )
        except Exception as e:
            logger.warning(f"list_associates failed: {e}", exc_info=True)
            return {"error": f"Failed to list associates: {str(e)}", "total": 0, "data": []}

    @tool
    async def get_associate_contact(search: str) -> dict[str, Any]:
        """Fast lookup for an associate's contact info (email, phone) matching a name."""
        try:
            return await integration.get_associate_contact(search=search, db=db)
        except Exception as e:
            logger.warning(f"get_associate_contact failed: {e}", exc_info=True)
            return {"error": f"Failed to look up contact for '{search}': {str(e)}"}

    @tool
    async def get_associate_assignments(associate_id: str) -> dict[str, Any]:
        """
        Find projects & events an associate is assigned to by their exact _id.
        Use list_associates to find the _id first.
        """
        try:
            return await integration.get_associate_assignments(associate_id=associate_id, db=db)
        except Exception as e:
            logger.warning(f"get_associate_assignments failed: {e}", exc_info=True)
            return {"error": f"Failed to get assignments for '{associate_id}': {str(e)}"}

    @tool
    async def list_events(
        vertical: Optional[str] = None,
        search: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        unassigned_only: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Flat list of events across all projects. Dates must be ISO strings.
        - `search`: Matches event type (e.g. "Reception"), client/groom/bride/child name, company name,
          or project code. Use this for queries like "When is Aswin's reception?" with search="Aswin".
        - `from_date`/`to_date`: Filter by date range.
        - `unassigned_only`: Only events with no team assigned.
        """
        try:
            return await integration.list_events(
                vertical=vertical,
                search=search,
                from_date=from_date,
                to_date=to_date,
                unassigned_only=unassigned_only,
                limit=limit,
                db=db,
            )
        except Exception as e:
            logger.warning(f"list_events failed: {e}", exc_info=True)
            return {"error": f"Failed to list events: {str(e)}", "total": 0, "data": []}

    @tool
    async def list_verticals() -> dict[str, Any]:
        """Get active verticals and their project counts."""
        try:
            return await integration.list_verticals(db=db)
        except Exception as e:
            logger.warning(f"list_verticals failed: {e}", exc_info=True)
            return {"error": f"Failed to list verticals: {str(e)}"}

    @tool
    async def get_statistics(
        module: Literal["dashboard", "finance", "projects", "clients", "associates"],
        vertical: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Get aggregate stats. module="dashboard": overall system stats.
        module="finance": totals and net profit.
        module="projects": active/ongoing breakdown (can optionally filter by vertical).
        """
        try:
            if module == "dashboard":
                return await integration.get_dashboard_stats(db=db)
            elif module == "finance":
                return await integration.get_finance_overview(db=db)
            elif module == "projects":
                return await integration.get_project_stats(vertical=vertical, db=db)
            elif module == "clients":
                return await integration.get_client_stats(db=db)
            elif module == "associates":
                return await integration.get_associate_stats(db=db)
            else:
                return {"error": f"Unknown module '{module}'. Valid: dashboard, finance, projects, clients, associates"}
        except Exception as e:
            logger.warning(f"get_statistics failed for module '{module}': {e}", exc_info=True)
            return {"error": f"Failed to get {module} statistics: {str(e)}"}

    return [
        list_projects,
        get_project_details,
        list_clients,
        list_associates,
        get_associate_contact,
        get_associate_assignments,
        list_events,
        list_verticals,
        get_statistics,
    ]
