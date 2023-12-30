from functools import wraps
from flask_login import LoginManager, login_user, current_user
import hiddifypanel.hutils as hutils
from flask import g, request
from apiflask import abort
from flask import current_app
from hiddifypanel.models import AdminUser, User, get_admin_by_uuid, Role
from hiddifypanel.models.user import get_user_by_uuid


def init_app(app):
    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def user_loader_auth(id: str) -> User | AdminUser | None:
        # first of all check if user sent Authorization header, our priority is with Authorization header
        if request.headers.get("Authorization"):
            return request_loader_auth(request)

        # parse id
        account_type, id = hutils.utils.parse_auth_id(id)  # type: ignore
        if not account_type or not id:
            return

        if account_type == type(AdminUser):
            account = AdminUser.query.filter(AdminUser.id == id).first()
        else:
            account = User.query.filter(User.id == id).first()

        if account:
            g.account = account
            g.account_uuid = account.uuid
            g.is_admin = False if account.role == Role.user else True
        return account

    @login_manager.request_loader
    def request_loader_auth(request) -> User | AdminUser | None:
        auth_header: str = request.headers.get("Authorization")
        if not auth_header:
            return

        account = None
        is_api_request = False
        if not request.blueprint:
            return
        if 'api' in request.blueprint:
            if apikey := hutils.utils.get_apikey_from_auth_header(auth_header):
                account = get_user_by_uuid(apikey) or get_admin_by_uuid(apikey)
                is_api_request = True
        else:
            if username_password := hutils.utils.parse_basic_auth_header(auth_header):
                if request.blueprint == 'user2':
                    account = User.query.filter(User.username == username_password[0], User.password == username_password[1]).first()
                else:
                    account = AdminUser.query.filter(AdminUser.username == username_password[0], AdminUser.password == username_password[1]).first()

        if account:
            g.account = account
            g.account_uuid = account.uuid
            g.is_admin = False if account.role == 'user' else True
            if not is_api_request:
                login_user(account)
        return account

    @login_manager.unauthorized_handler
    def unauthorized():
        # TODO: show the login page

        abort(401, "Unauthorized")


def login_required(roles: set[Role] | None = None):
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()  # type: ignore
            if roles:
                # super_admin role has admin role permission too
                if Role.admin in roles and Role.super_admin not in roles:
                    roles.add(Role.super_admin)
                account_role = current_user.role
                if account_role not in roles:
                    return current_app.login_manager.unauthorized()  # type: ignore
            return fn(*args, **kwargs)
        return decorated_view
    return wrapper