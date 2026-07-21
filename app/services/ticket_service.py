# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List, Optional
import uuid

def get_ticket_details(db: Session, ticket_id: str) -> Optional[Dict[str, Any]]:
    # Check if the input is a valid UUID format
    is_uuid = False
    try:
        uuid.UUID(ticket_id)
        is_uuid = True
    except ValueError:
        is_uuid = False

    if is_uuid:
        # Search by uniqueidentifier GUID primary key
        query = """
        SELECT 
            t.id AS id, 
            t.ticketNo AS ticketNo, 
            t.title AS title, 
            t.description AS description, 
            t.priority AS priority, 
            t.status AS status, 
            t.assignTo AS assignTo,
            e.name AS AssigneeName,
            e.empId AS AssigneeEmpId,
            p.name AS ProjectName,
            (
                SELECT STRING_AGG(c.name, ', ') 
                FROM TblTicketCategory tc
                JOIN TblCategory c ON tc.categoryId = c.id
                WHERE tc.ticketId = t.id
            ) AS category
        FROM TblTicketRegistration t
        LEFT JOIN TblEmployee e ON t.assignTo = e.id
        LEFT JOIN TblProject p ON t.projectId = p.id
        WHERE t.id = :ticket_id
        """
    else:
        # Search directly by ticketNo string (e.g. 'ATS300426002')
        query = """
        SELECT 
            t.id AS id, 
            t.ticketNo AS ticketNo, 
            t.title AS title, 
            t.description AS description, 
            t.priority AS priority, 
            t.status AS status, 
            t.assignTo AS assignTo,
            e.name AS AssigneeName,
            e.empId AS AssigneeEmpId,
            p.name AS ProjectName,
            (
                SELECT STRING_AGG(c.name, ', ') 
                FROM TblTicketCategory tc
                JOIN TblCategory c ON tc.categoryId = c.id
                WHERE tc.ticketId = t.id
            ) AS category
        FROM TblTicketRegistration t
        LEFT JOIN TblEmployee e ON t.assignTo = e.id
        LEFT JOIN TblProject p ON t.projectId = p.id
        WHERE t.ticketNo = :ticket_id
        """
        
    row = db.execute(text(query), {"ticket_id": ticket_id}).mappings().first()
    return dict(row) if row else None

def get_all_employees(db: Session) -> List[Dict[str, Any]]:
    # Retrieve employee name, employee ID, and designation
    query = """
    SELECT 
        e.name AS Name,
        e.empId AS EmpId,
        d.name AS Designation
    FROM TblEmployee e
    LEFT JOIN tblDesignation d ON e.designationId = d.Id
    WHERE e.status = 1 OR e.status IS NULL
    """
    rows = db.execute(text(query)).mappings().all()
    return [dict(row) for row in rows]

def get_employee_project_workloads(db: Session) -> List[Dict[str, Any]]:
    query = """
    SELECT 
        e.name AS EmployeeName,
        e.empId AS EmployeeId,
        p.name AS ProjectName,
        COUNT(t.id) AS TicketCount
    FROM TblEmployee e
    JOIN TblTicketRegistration t ON t.assignTo = e.id
    JOIN TblProject p ON t.projectId = p.id
    WHERE t.status IN ('In Process', 'Pending on Server')
    GROUP BY e.name, e.empId, p.name
    """
    rows = db.execute(text(query)).mappings().all()
    return [dict(row) for row in rows]


def get_active_tickets_candidates(
    db: Session, 
    entity_id: str, 
    block_id: str, 
    client_id: str, 
    project_id: str
) -> List[Dict[str, Any]]:
    query = """
    SELECT TOP 50
        t.id AS ticketId, 
        t.ticketNo AS ticketNo, 
        t.status AS status, 
        t.priority AS priority, 
        t.description AS description
    FROM TblTicketRegistration t
    WHERE t.entityId = :entity_id
      AND t.blockId = :block_id
      AND t.clientId = :client_id
      AND t.projectId = :project_id
      AND t.status IN (
        'Raised',
        'In Process',
        'Pending on Server',
        'Overdue',
        'Overdue Pending on Server',
        'Re Open',
        'ReOpen Pending on Server'
      )
    ORDER BY t.createdDate DESC
    """
    rows = db.execute(
        text(query), 
        {
            "entity_id": entity_id,
            "block_id": block_id,
            "client_id": client_id,
            "project_id": project_id
        }
    ).mappings().all()
    return [dict(row) for row in rows]

