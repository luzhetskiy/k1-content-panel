"""CRUD пользователей панели: список, создание, правка, удаление.

Защита последнего администратора (в create/update/delete) — без неё снятие
роли или деактивация единственного активного admin'а заперла бы админку
снаружи, а починить это можно было бы только прямой правкой БД в обход
приложения (require_role("admin") стоит на самом /api/admin/users).

Гонка при одновременной демоции/деактивации ДВУХ последних активных
админов: `_count_active_admins` — обычный SELECT без блокировки строк
(`SELECT ... FOR UPDATE`). Два параллельных PUT на двух РАЗНЫХ последних
администраторах теоретически могут оба увидеть "остаётся ещё один" и оба
пройти проверку до того, как любой из них закоммитит, — тогда оба commit
пройдут и админов не останется вовсе. Осознанно не чиним: для панели на
2–3 человека сценарий требует, чтобы два администратора ОДНОВРЕМЕННО (в
пределах одного HTTP round-trip) демоутили или деактивировали ДРУГ ДРУГА —
на порядок менее вероятно, чем гонка двойной оплаты по одному клику
(Task 18), где для срабатывания достаточно ОДНОГО пользователя с плохим
сетевым соединением. `SELECT ... FOR UPDATE` здесь добавил бы блокировку
строк users на каждый PUT ради закрытия сценария, у которого в проде на
несколько человек практическая вероятность неотличима от нуля.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.api.security import hash_password
from app.models.user import User

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

ROLES = ("admin", "manager")


class UserIn(BaseModel):
    email: str
    full_name: str
    role: str = "manager"
    password: str = Field(default="", min_length=0)   # пусто при правке = «не менять»
    is_active: bool = True


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool


def _to_out(user: User) -> UserOut:
    # Явный список полей, а не UserOut.model_validate(user, from_attributes=True):
    # если в User когда-нибудь добавят новое поле (например, password_hash уже
    # есть), автосборка из атрибутов молча утащит его в ответ API при первом же
    # использовании .model_validate — здесь же лишнее поле пришлось бы вписывать
    # руками и его отсутствие сразу бросится в глаза на код-ревью.
    return UserOut(id=user.id, email=user.email, full_name=user.full_name,
                   role=user.role, is_active=user.is_active)


def _count_active_admins(db: Session, exclude_id: int | None = None) -> int:
    query = select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True))
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    return db.scalar(query) or 0


def _normalize_email(email: str) -> str:
    # Зеркалит login() (app/api/auth.py) и create_admin.py: колонка email
    # регистрозависима на уровне БД (unique=True не различает "Ivan@k1.ru" и
    # "ivan@k1.ru" сама по себе), поэтому без .lower() завелись бы два разных
    # пользователя с визуально одинаковым адресом, а войти удалось бы только
    # тем написанием, которым создавали.
    return email.strip().lower()


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    return [_to_out(u) for u in db.scalars(select(User).order_by(User.email)).all()]


@router.post("", response_model=UserOut)
def create_user(payload: UserIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    if payload.role not in ROLES:
        raise HTTPException(400, f"неизвестная роль: {payload.role}")
    if len(payload.password) < 8:
        raise HTTPException(422, "пароль короче 8 символов")
    email = _normalize_email(payload.email)
    if db.scalars(select(User).where(User.email == email)).first():
        raise HTTPException(400, f"пользователь {email} уже существует")

    try:
        password_hash = hash_password(payload.password)
    except ValueError as e:
        # hash_password бросает ValueError на пароле длиннее 72 байт —
        # без перехвата это ушло бы наружу 500-й (см. app/api/security.py).
        raise HTTPException(422, str(e))

    user = User(email=email, full_name=payload.full_name, role=payload.role,
                is_active=payload.is_active, password_hash=password_hash)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Конкурентное создание: между нашим SELECT-проверкой чуть выше
        # (промах) и INSERT кто-то другой уже завёл пользователя с этим же
        # email — тот же класс гонки, что уже закрыт в SettingsService._upsert
        # (Task 5) и save_prompt (Task 12) через rollback + повтор. Здесь
        # повтор как UPDATE не подходит: это не upsert одной сущности по
        # ключу, а строгое создание нового пользователя — превращать его в
        # тихую правку чужого существующего аккаунта было бы неожиданным
        # поведением. Поэтому просто рапортуем конфликт, как и при обычном
        # (не гоночном) дубле чуть выше.
        db.rollback()
        raise HTTPException(400, f"пользователь {email} уже существует")
    return _to_out(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserIn, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "пользователь не найден")
    if payload.role not in ROLES:
        raise HTTPException(400, f"неизвестная роль: {payload.role}")

    # Снятие роли или деактивация последнего активного админа заперла бы
    # админку снаружи — см. докстринг модуля про гонку на этой же проверке.
    losing_admin = user.role == "admin" and (payload.role != "admin" or not payload.is_active)
    if losing_admin and _count_active_admins(db, exclude_id=user.id) == 0:
        raise HTTPException(400, "это последний активный администратор")

    email = _normalize_email(payload.email)
    if email != user.email and db.scalars(select(User).where(User.email == email)).first():
        # Без этой проверки смена почты на чужую упала бы на unique-констрейнте
        # необработанным IntegrityError — 500 вместо внятного 400.
        raise HTTPException(400, f"пользователь {email} уже существует")

    user.email = email
    user.full_name = payload.full_name
    user.role = payload.role
    user.is_active = payload.is_active
    if payload.password:
        if len(payload.password) < 8:
            raise HTTPException(422, "пароль короче 8 символов")
        try:
            user.password_hash = hash_password(payload.password)
        except ValueError as e:
            raise HTTPException(422, str(e))

    try:
        db.commit()
    except IntegrityError:
        # Тот же класс гонки, что и в create_user: между SELECT-проверкой
        # занятости email чуть выше и этим commit кто-то другой успел занять
        # тот же адрес. Откатываем и рапортуем конфликт, ничего не повторяем —
        # обновление это не upsert по email, а правка конкретного user_id.
        db.rollback()
        raise HTTPException(400, f"пользователь {email} уже существует")
    return _to_out(user)


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                _user: User = Depends(require_role("admin"))):
    """До Task 19 FK article_batches.created_by_id/job_runs.created_by_id ->
    users.id были объявлены без ON DELETE (Postgres — NO ACTION по
    умолчанию): удаление ЛЮБОГО пользователя, хоть раз создавшего партию
    статей или запустившего фоновую задачу, падало необработанным
    IntegrityError → 500 (проверено вручную на живом Postgres, см. миграцию
    450fdec97dd5_created_by_id_set_null_on_delete.py). Партии создаются
    менеджером ежедневно, поэтому это ломало delete_user практически для
    любого реально работающего пользователя панели, а не только в редком
    крае. Оба FK переведены на ON DELETE SET NULL — партия/задача это
    журнал того, что было реально сделано (тот же принцип, что и у site_id
    в этих же моделях, Task 14), и должны пережить удаление автора, а не
    блокировать его удаление. После этой миграции db.delete(user) ниже
    больше не требует предварительной проверки на ArticleBatch/JobRun —
    Postgres сам обнулит created_by_id в момент commit.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "пользователь не найден")
    if user.role == "admin" and _count_active_admins(db, exclude_id=user.id) == 0:
        raise HTTPException(400, "это последний активный администратор")
    db.delete(user)
    db.commit()
    return {"ok": True}
