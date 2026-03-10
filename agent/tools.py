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
    from typing import Literal

    @tool
    async def list_projects(
        vertical: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        List projects. 
        Use `vertical` if the user specifies a category/vertical name (e.g. "Wedding", "Real Estate", or "Pluto").
        Use `search` if the user specifies a specific client name or project code.
        If unsure whether a term is a vertical or client name, try `vertical` first or call `list_verticals`.
        """
        return await integration.list_projects(
            vertical=vertical, status=status, search=search, page=page, limit=limit, db=db
        )

    @tool
    async def get_project_details(
        id_or_code: str,
        view: Literal["full", "team", "schedule", "deliverables"] = "full"
    ) -> dict[str, Any]:
        """
        Fetch details for a project by exact _id or code (e.g. 'KN-39829'). 
        view="full": All details.
        view="team": Event names, dates, and assigned associates.
        view="schedule": Event names, dates, and venues.
        view="deliverables": Incomplete deliverables.
        """
        if view == "team":
            return await integration.get_project_team(identifier=id_or_code, db=db)
        elif view == "schedule":
            return await integration.get_project_schedule(identifier=id_or_code, db=db)
        elif view == "deliverables":
            return await integration.get_pending_deliverables(identifier=id_or_code, db=db)
        else:
            return await integration.get_project(identifier=id_or_code, db=db)

    @tool
    async def list_clients(
        type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List clients. Use search to find by exact name or phone."""
        return await integration.list_clients(
            client_type=type, search=search, page=page, limit=limit, db=db
        )

    @tool
    async def list_associates(
        role: Optional[str] = None,
        employment_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List associates (team members). Use search by name or phone."""
        return await integration.list_associates(
            role=role,
            employment_type=employment_type,
            search=search,
            page=page,
            limit=limit,
            db=db,
        )

    @tool
    async def get_associate_contact(search: str) -> dict[str, Any]:
        """Fast lookup for an associate's contact info (email, phone) matching a name."""
        return await integration.get_associate_contact(search=search, db=db)

    @tool
    async def get_associate_assignments(associate_id: str) -> dict[str, Any]:
        """
        Find projects & events an associate is assigned to by their exact _id.
        Use list_associates to find the _id first.
        """
        return await integration.get_associate_assignments(associate_id=associate_id, db=db)

    @tool
    async def list_events(
        vertical: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        unassigned_only: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Flat list of events across projects. Dates must be ISO strings."""
        return await integration.list_events(
            vertical=vertical,
            from_date=from_date,
            to_date=to_date,
            unassigned_only=unassigned_only,
            limit=limit,
            db=db,
        )

    @tool
    async def list_verticals() -> dict[str, Any]:
        """Get active verticals and their project counts."""
        return await integration.list_verticals(db=db)

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
            return {"error": "Invalid module."}

    # Return the full suite of bound tools
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
