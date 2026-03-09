from typing import Optional, Any
from langchain_core.tools import BaseTool, tool
from middleware.db_guard import ScopedDatabase

# Import existing core logic directly to reuse it, avoiding HTTP calls
from routes import integration

def build_tools(db: ScopedDatabase) -> list[BaseTool]:
    """
    Factory to create all available tools, bound to the specified ScopedDatabase.
    The LLM never queries the database directly; it only invokes these 
    predefined, strictly typed tools.
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
        List projects. You can scope to a vertical or search globally by client name or code.
        Use this BEFORE returning if you only know a client's name. Example: search="Vihaan Patel"
        """
        return await integration.list_projects(
            vertical=vertical,
            status=status,
            search=search,
            page=page,
            limit=limit,
            db=db,
        )

    @tool
    async def get_project_stats(vertical: Optional[str] = None) -> dict[str, Any]:
        """Get high-level statistics about projects: total, active, ongoing, and this month."""
        return await integration.get_project_stats(vertical=vertical, db=db)

    @tool
    async def get_project_by_strict_id_or_code(project_id_or_code: str) -> dict[str, Any]:
        """
        Fetch full details for a single project strictly by its MongoDB _id OR its exact string code (e.g. 'KN-39829').
        DO NOT pass a client's name here. If you only know a client's name, use `list_projects(search="Name")` first to find the project ID or code.
        """
        return await integration.get_project(identifier=project_id_or_code, db=db)

    @tool
    async def list_clients(
        type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List clients. Use 'search' to find by name or phone."""
        return await integration.list_clients(
            client_type=type, search=search, page=page, limit=limit, db=db
        )

    @tool
    async def get_client_stats() -> dict[str, Any]:
        """Client overview statistics."""
        return await integration.get_client_stats(db=db)

    @tool
    async def list_associates(
        role: Optional[str] = None,
        employment_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List associates (team members). Use 'search' to find by name or phone."""
        return await integration.list_associates(
            role=role,
            employment_type=employment_type,
            search=search,
            page=page,
            limit=limit,
            db=db,
        )

    @tool
    async def get_associate_stats() -> dict[str, Any]:
        """Associate overview stats (total, inhouse, freelance)."""
        return await integration.get_associate_stats(db=db)

    @tool
    async def list_events(
        vertical: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        unassigned_only: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Flat listing of events across all projects.
        Useful for queries like 'upcoming events', 'events next week', or 'unassigned events'.
        Dates must be ISO strings (e.g., '2026-03-01').
        """
        return await integration.list_events(
            vertical=vertical,
            from_date=from_date,
            to_date=to_date,
            unassigned_only=unassigned_only,
            limit=limit,
            db=db,
        )

    @tool
    async def get_associate_assignments(associate_id: str) -> dict[str, Any]:
        """
        Find all projects & events a specific associate is assigned to.
        Requires the associate's exact MongoDB _id.
        IMPORTANT: If the user only provides an associate's name, you MUST use the `list_associates` tool first with the `search` parameter to find their `_id`. DO NOT guess the `_id`.
        """
        return await integration.get_associate_assignments(
            associate_id=associate_id, db=db
        )

    @tool
    async def list_verticals() -> dict[str, Any]:
        """Get a list of all active verticals and their project counts."""
        return await integration.list_verticals(db=db)

    @tool
    async def get_dashboard_stats() -> dict[str, Any]:
        """High-level system stats: active projects, total clients, total associates, pending tasks."""
        return await integration.get_dashboard_stats(db=db)

    @tool
    async def get_finance_overview() -> dict[str, Any]:
        """Finance summary: total income, expenses, net profit, and outstanding receivables."""
        return await integration.get_finance_overview(db=db)

    @tool
    async def get_project_team(project_id_or_code: str) -> dict[str, Any]:
        """
        Token-saver: Fast, lightweight way to fetch only event names, dates, and assigned associates for a project. 
        Always prefer this over `get_project` when asked "Who is assigned to..." or "What is the team for...".
        """
        return await integration.get_project_team(identifier=project_id_or_code, db=db)

    @tool
    async def get_project_schedule(project_id_or_code: str) -> dict[str, Any]:
        """
        Token-saver: Fast, lightweight way to fetch only event names, dates, and venues for a project.
        Always prefer this over `get_project` when asked about schedules.
        """
        return await integration.get_project_schedule(identifier=project_id_or_code, db=db)

    @tool
    async def get_pending_deliverables(project_id_or_code: str) -> dict[str, Any]:
        """
        Token-saver: Fast, lightweight way to fetch only the pending (incomplete) deliverables for a project.
        Always prefer this over `get_project` when asked about remaining deliverables or what is left to do.
        """
        return await integration.get_pending_deliverables(identifier=project_id_or_code, db=db)
        
    @tool
    async def get_associate_contact(search: str) -> dict[str, Any]:
        """
        Token-saver: Fast, lightweight way to fetch only contact info (name, email, phone, role) for associates matching a name.
        Always prefer this over `list_associates` when simply asked for contact details.
        """
        return await integration.get_associate_contact(search=search, db=db)

    # Return the full suite of bound tools
    return [
        list_projects,
        get_project_stats,
        get_project_by_strict_id_or_code,
        list_clients,
        get_client_stats,
        list_associates,
        get_associate_stats,
        get_associate_contact,
        list_events,
        get_associate_assignments,
        list_verticals,
        get_dashboard_stats,
        get_finance_overview,
        get_project_team,
        get_project_schedule,
        get_pending_deliverables,
    ]
