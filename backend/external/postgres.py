import importlib
import pkgutil

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from typing import Any, Optional, Literal
from sqlalchemy import text, select, desc, asc, update
from sqlalchemy.exc import SQLAlchemyError

from external.base import ExternalService
from settings import settings
from logger import logger

import models.db
from models.db.base import Base
from exceptions.exceptions import ExternalServiceException


def register_models() -> list[str]:
    """Import every module under models.db, and report the tables that appear.

    A declarative class attaches itself to Base.metadata when its module is
    *executed*, so a model nobody imported does not exist as far as SQLAlchemy
    is concerned — and create_all builds from that metadata, not from the
    directory. The tables it creates are therefore decided by whoever happened
    to import what.

    This used to be four `from models.db.x import Y  # noqa: F401` lines. They
    were correct and they were deleted, because the noqa silences the linter's
    warning but not an editor's "remove unused imports" — to any cleanup they
    read as exactly what they look like. Discovery cannot be tidied away: there
    is no unused name to remove, and a new model file registers itself by
    existing rather than by being added to a list.
    """
    for module in pkgutil.iter_modules(models.db.__path__):
        importlib.import_module(f"{models.db.__name__}.{module.name}")
    return sorted(Base.metadata.tables)


#: At import, not at startup. A mapper has to be registered before anything
#: queries it, not merely before the schema is built — an unregistered model
#: also breaks foreign key resolution on the models that reference it, which is
#: how a run insert started failing with "could not find table 'sessions'".
#: Importing this module is therefore enough; nobody has to call anything.
TABLES = register_models()


class PostgresService(ExternalService):
    def __init__(self):
        self.engine = None
        self.async_db_session_factory = None

    async def startup(self):
        logger.info("Starting up Postgres Service")
        postgres_url = f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        self.engine = create_async_engine(
            postgres_url,  # e.g. postgresql+asyncpg://user:pass@host/db
            # echo=True,  # Enable SQL echo for debugging
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,
        )
        self.async_db_session_factory = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=AsyncSession
        )

        try:
            await self._migrate()
            async with self.engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                    )
                )
                logger.info(
                    f"Startup Completed: available tables: {result.scalars().all()}"
                )
        except ExternalServiceException:
            raise
        except Exception as e:
            msg = f"Failed to connect to Postgres: {str(e)}"
            logger.error(msg)
            raise ExternalServiceException(msg, "PostgresService")

    async def _migrate(self) -> None:
        """Create any table that does not exist yet.

        There are no migrations. This creates missing TABLES and does nothing
        whatsoever to existing ones — it never issues ALTER — so changing a
        model means dropping the database:

            docker compose down -v && docker compose up -d

        Every model is registered first, so what gets created is the contents of
        models/db rather than whatever this process happened to import.
        """
        try:
            logger.info(f"Creating any missing tables: {TABLES}")
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception as e:
            msg = f"Schema creation failed: {e}"
            logger.error(msg)
            raise ExternalServiceException(msg, "PostgresService")

    async def sanity_check(self):
        logger.info("Sanity checking Postgres Service")
        try:
            async with self.engine.begin() as conn:
                _ = await conn.execute(text("SELECT 1"))
        except SQLAlchemyError as e:
            msg = f"Sanity check failed: {str(e)}"
            logger.error(msg)
            raise ExternalServiceException(msg, "PostgresService")
        logger.info("Sanity check completed")

    async def shutdown(self):
        logger.info("Shutting down Postgres Service")
        await self.engine.dispose()
        logger.info("Shutdown completed")

    def _get_db_session(self) -> AsyncSession:
        return self.async_db_session_factory()

    @staticmethod
    def get_id(db_obj):
        # Get primary key values
        id_info = ", ".join(
            f"{key.name}={getattr(db_obj, key.name)}"
            for key in db_obj.__table__.primary_key.columns
        )
        return f"{id_info}"

    async def add(self, db_obj):
        async with self._get_db_session() as db_session:
            try:
                db_session.add(db_obj)
                await db_session.commit()
                await db_session.refresh(db_obj)
                logger.info(
                    f"Successfully added {db_obj.__class__.__name__} with id {self.get_id(db_obj)} in Database"
                )
                return db_obj
            except SQLAlchemyError as e:
                await db_session.rollback()
                msg = f"Add failed for {db_obj.__class__.__name__} with id {self.get_id(db_obj)}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")

    async def update(self, model, db_obj, updates: dict):
        async with self._get_db_session() as db_session:
            try:
                ## build up prim key tags and values
                pk = {
                    c.name: getattr(db_obj, c.name)
                    for c in db_obj.__table__.primary_key.columns
                }
                # find matching rows
                query = select(model)
                for key, value in pk.items():
                    query = query.where(getattr(model, key) == value)
                current = (await db_session.execute(query)).scalar_one_or_none()
                if current is None:
                    msg = f"{model.__name__} with id {self.get_id(db_obj)} no longer exists"
                    logger.error(msg)
                    raise ExternalServiceException(msg, "PostgresService")
                # deliver update
                for key, value in updates.items():
                    setattr(current, key, value)
                await db_session.commit()
                await db_session.refresh(current)
                logger.info(
                    f"Successfully updated {model.__name__} with id {self.get_id(current)} in Database"
                )
                return current
            except SQLAlchemyError as e:
                await db_session.rollback()
                msg = f"Update failed for {model.__name__} with id {self.get_id(db_obj)}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")

    async def delete(self, db_obj):
        async with self._get_db_session() as db_session:
            try:
                await db_session.delete(db_obj)
                await db_session.commit()
                logger.info(
                    f"Successfully deleted {db_obj.__class__.__name__} with id {self.get_id(db_obj)} from Database"
                )
                return True
            except SQLAlchemyError as e:
                await db_session.rollback()
                msg = f"Delete failed for {db_obj.__class__.__name__} with id {self.get_id(db_obj)}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")

    async def get(
        self,
        model,
        obj_id: Any = None,
        is_composite: bool = False,
        composite_keys: dict[str, Any] = None,
    ):
        """Get an object by ID.
        * Provide either obj_id or is_composite + composite_keys
        * Failure (not found) will return None
        * Not found errors are handled in service layer to support complex logic
        """
        async with self._get_db_session() as db_session:
            if is_composite and not composite_keys:
                msg = "composite_keys must be provided when is_composite is True"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")
            elif not is_composite and not obj_id:
                msg = "obj_id must be provided when is_composite is False"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")
            try:
                if is_composite:
                    query = select(model)
                    for key, value in composite_keys.items():
                        query = query.where(getattr(model, key) == value)
                    result = await db_session.execute(query)
                    db_obj = result.scalar()
                else:
                    db_obj = await db_session.get(model, obj_id)
                if db_obj:
                    logger.info(
                        f"Successfully retrieved {model.__name__} with id {self.get_id(db_obj)} from Database"
                    )
                return db_obj

            except SQLAlchemyError as e:
                msg = f"Get failed for {model.__name__} with id {self.get_id(db_obj)}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")

    async def get_list(
        self,
        model,
        filters: dict[str, Any],
        skip: int = 0,
        limit: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Literal["asc", "desc"] = "desc",
    ) -> list[Any]:
        """List objects with pagination and sorting.

        Args:
            model: SQLAlchemy model class
            filters: Dictionary of filters to apply
            skip: Number of records to skip
            limit: Maximum number of records to return
            sort_by: Column name to sort by
            sort_order: Sort order ("asc" or "desc")
        """
        async with self._get_db_session() as db_session:
            try:
                # Start with base query
                query = select(model)

                # Apply filters
                for key, value in filters.items():
                    query = query.where(getattr(model, key) == value)

                # Apply sorting if specified
                if sort_by:
                    sort_column = getattr(model, sort_by)
                    if sort_order == "desc":
                        query = query.order_by(desc(sort_column))
                    else:
                        query = query.order_by(asc(sort_column))

                # Apply pagination
                if limit:
                    query = query.limit(limit)
                if skip:
                    query = query.offset(skip)

                # Execute query
                result = await db_session.execute(query)
                items = result.scalars().all()

                logger.info(
                    f"Successfully retrieved {len(items)} {model.__name__} records from Database"
                )
                return items
            except SQLAlchemyError as e:
                msg = f"List with pagination failed for {model.__name__}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")

    async def delete_list(self, model, filters: dict) -> int:
        """Delete multiple objects based on filters.

        Args:
            model: SQLAlchemy model class
            filters: Dictionary of filters to apply

        Returns:
            int: Number of deleted records
        """
        async with self._get_db_session() as db_session:
            try:
                # Get all items using list_with_pagination
                items = await self.get_list(model=model, filters=filters, skip=0)

                # Delete each item
                for item in items:
                    await db_session.delete(item)

                await db_session.commit()

                deleted_count = len(items)
                logger.info(
                    f"Successfully deleted {deleted_count} {model.__name__} records from Database"
                )
                return deleted_count

            except SQLAlchemyError as e:
                await db_session.rollback()
                msg = f"Delete list failed for {model.__name__}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")

    async def update_where(self, model, filters: dict, updates: dict) -> int:
        """Update every row matching `filters`, in one statement. Returns the
        number of rows changed.

        Distinct from `update`, which loads a row and writes it back: that is
        two statements with a gap between them, so two processes can both read
        the same row and both write. This issues a single UPDATE ... WHERE, and
        the row count says whether the caller's version of reality still held.

        Args:
            model: SQLAlchemy model class
            filters: column -> expected value, ANDed together
            updates: column -> new value

        Returns:
            int: number of rows updated
        """
        async with self._get_db_session() as db_session:
            try:
                query = update(model)
                for key, value in filters.items():
                    query = query.where(getattr(model, key) == value)
                result = await db_session.execute(query.values(**updates))
                await db_session.commit()
                logger.info(
                    f"Updated {result.rowcount} {model.__name__} records in Database"
                )
                return result.rowcount
            except SQLAlchemyError as e:
                await db_session.rollback()
                msg = f"Conditional update failed for {model.__name__}: {str(e)}"
                logger.error(msg)
                raise ExternalServiceException(msg, "PostgresService")


postgres_service = PostgresService()
