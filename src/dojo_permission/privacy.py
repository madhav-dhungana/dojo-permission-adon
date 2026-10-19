"""Scope DefectDojo's user selectors without changing its User manager."""

from functools import wraps
import sys

from crum import get_current_request, get_current_user
from django.contrib.auth import get_user_model

from dojo.authorization.roles_permissions import Permissions
from dojo.models import (
    Dojo_Group_Member,
    Product,
    Product_Group,
    Product_Member,
    Product_Type_Group,
    Product_Type_Member,
)

from dojo_permission.policy import (
    PRIVILEGED_ROLES,
    directly_assigned_product_ids,
    is_restricted_user,
    is_shared_restricted_user,
    product_for_object,
    restricted_membership_group_ids,
)

_installed = False


def _product_for_context(*, pid=None, eid=None, tid=None, finding_id=None):
    if finding_id is not None:
        return Product.objects.filter(engagement__test__finding__pk=finding_id).first()
    if tid is not None:
        return Product.objects.filter(engagement__test__pk=tid).first()
    if eid is not None:
        return Product.objects.filter(engagement__pk=eid).first()
    return Product.objects.filter(pk=pid).first() if pid is not None else None


def request_product(request=None):
    request = request or get_current_request()
    if request is None:
        return None
    match = getattr(request, "resolver_match", None)
    values = getattr(match, "kwargs", {}) or {}
    view_class = getattr(getattr(match, "func", None), "cls", None)
    api_context = {
        "FindingViewSet": "finding_id", "EngagementViewSet": "eid",
        "TestsViewSet": "tid", "ProductViewSet": "pid",
    }
    context = {}
    if "pk" in values and getattr(view_class, "__name__", None) in api_context:
        context[api_context[view_class.__name__]] = values["pk"]
    product = _product_for_context(
        pid=context.get("pid", values.get("pid", values.get("product_id"))),
        eid=context.get("eid", values.get("eid", values.get("engagement_id"))),
        tid=context.get("tid", values.get("tid", values.get("test_id"))),
        finding_id=context.get("finding_id", values.get("fid", values.get("finding_id"))),
    )
    if product is not None:
        return product
    for key in ("test__engagement__product", "product", "product_id"):
        selected = request.GET.getlist(key)
        if len(selected) == 1 and str(selected[0]).isdigit():
            return _product_for_context(pid=selected[0])
    return None


def visible_user_ids(user, product=None) -> set[int]:
    """Only explicit participants in the requester's visible products plus self."""
    User = get_user_model()
    if not is_restricted_user(user):
        return set(User.objects.values_list("pk", flat=True))
    if is_shared_restricted_user(user):
        product_ids = directly_assigned_product_ids(user)
        if product is not None:
            product_ids = product_ids.filter(product_id=product.pk)
        ids = {user.pk}
        ids.update(
            Product_Member.objects.filter(
                product_id__in=product_ids,
            ).values_list("user_id", flat=True),
        )
        return ids

    from dojo.product.queries import get_authorized_products

    products = get_authorized_products(Permissions.Product_View, user=user)
    if product is not None:
        products = products.filter(pk=product.pk)
    product_ids = set(products.values_list("pk", flat=True))
    if not product_ids:
        return {user.pk}
    type_ids = set(Product.objects.filter(pk__in=product_ids).values_list("prod_type_id", flat=True))
    group_ids = set(Product_Group.objects.filter(product_id__in=product_ids).values_list("group_id", flat=True))
    group_ids.update(Product_Type_Group.objects.filter(product_type_id__in=type_ids).values_list("group_id", flat=True))
    ids = {user.pk}
    ids.update(Product_Member.objects.filter(product_id__in=product_ids).values_list("user_id", flat=True))
    ids.update(Product_Type_Member.objects.filter(product_type_id__in=type_ids).values_list("user_id", flat=True))
    ids.update(Dojo_Group_Member.objects.filter(group_id__in=group_ids).values_list("user_id", flat=True))
    return ids


allowed_product_scoped_visible_user_ids = visible_user_ids


def visible_users(user, product=None):
    users = get_user_model().objects.all()
    return users.filter(pk__in=visible_user_ids(user, product)) if is_restricted_user(user) else users


def visible_group_ids(user) -> set[int]:
    from dojo.product.queries import get_authorized_products

    products = get_authorized_products(Permissions.Product_View, user=user)
    type_ids = products.values("prod_type_id")
    ids = set(Dojo_Group_Member.objects.filter(user=user).values_list("group_id", flat=True))
    ids.update(Product_Group.objects.filter(product__in=products).values_list("group_id", flat=True))
    ids.update(Product_Type_Group.objects.filter(product_type_id__in=type_ids).values_list("group_id", flat=True))
    return ids


def replace_imported_references(original, replacement):
    """Keep aliases imported before AppConfig.ready consistent with later imports."""
    for name, module in tuple(sys.modules.items()):
        if not name.startswith("dojo.") or module is None:
            continue
        for attribute, value in tuple(vars(module).items()):
            if value is original:
                setattr(module, attribute, replacement)


def _instance_product(instance):
    if instance is None or isinstance(instance, (list, tuple)):
        return None
    try:
        return product_for_object(instance)
    except (AttributeError, Product.DoesNotExist):
        return None


def _is_user_queryset(queryset):
    model = getattr(queryset, "model", None)
    return model is not None and model._meta.concrete_model is get_user_model()._meta.concrete_model


def _scope_fields(fields, user, product=None):
    if not is_restricted_user(user):
        return
    ids = visible_user_ids(user, product or request_product())
    for field in fields.values():
        relation = getattr(field, "child_relation", field)
        queryset = getattr(relation, "queryset", None)
        if _is_user_queryset(queryset):
            relation.queryset = queryset.filter(pk__in=ids)


def _install_user_query_wrappers(queries):
    original = queries.get_authorized_users

    @wraps(original)
    def scoped_users(permission, user=None):
        result = original(permission, user=user)
        actor = user if user is not None else get_current_user()
        return result.filter(pk__in=visible_user_ids(actor, request_product())) if is_restricted_user(actor) else result

    replace_imported_references(original, scoped_users)
    queries.get_authorized_users = scoped_users
    original_product = queries.get_authorized_users_for_product_and_product_type

    @wraps(original_product)
    def scoped_product_users(users, product, permission):
        result = original_product(users, product, permission)
        actor = get_current_user()
        return result.filter(pk__in=visible_user_ids(actor, product)) if is_restricted_user(actor) else result

    replace_imported_references(original_product, scoped_product_users)
    queries.get_authorized_users_for_product_and_product_type = scoped_product_users
    original_type = queries.get_authorized_users_for_product_type

    @wraps(original_type)
    def scoped_type_users(users, product_type, permission):
        result = original_type(users, product_type, permission)
        actor = get_current_user()
        return result.filter(pk__in=visible_user_ids(actor)) if is_restricted_user(actor) else result

    replace_imported_references(original_type, scoped_type_users)
    queries.get_authorized_users_for_product_type = scoped_type_users


def _install_member_queries(module, name, *, groups=False, members=False, product_argument=False):
    original = getattr(module, name)

    @wraps(original)
    def scoped(*args, **kwargs):
        result = original(*args, **kwargs)
        actor = get_current_user()
        if not is_restricted_user(actor):
            return result
        if is_shared_restricted_user(actor):
            model_name = getattr(getattr(result, "model", None), "_meta", None)
            model_name = getattr(model_name, "model_name", None)
            if model_name in {"product_member", "product_group"}:
                result = result.filter(
                    product_id__in=directly_assigned_product_ids(actor),
                )
            elif model_name in {"product_type_member", "product_type_group"}:
                return result.none()
        product = None
        if product_argument:
            product = kwargs.get("product")
            if product is None and args:
                product = args[0]
        if getattr(getattr(product, "_meta", None), "model_name", None) != "product":
            product = None
        if groups:
            return result.filter(pk__in=visible_group_ids(actor))
        result = result.filter(user_id__in=visible_user_ids(actor, product))
        return result.filter(group_id__in=visible_group_ids(actor)) if members else result

    replace_imported_references(original, scoped)
    setattr(module, name, scoped)


def _install_form_privacy(module):
    from django.forms import BaseForm

    seen = set()
    for form_class in vars(module).values():
        if not isinstance(form_class, type) or not issubclass(form_class, BaseForm) or form_class in seen:
            continue
        if form_class.__module__ != module.__name__:
            continue
        seen.add(form_class)
        original = form_class.__init__

        def initialize(self, *args, _original=original, **kwargs):
            _original(self, *args, **kwargs)
            _scope_fields(self.fields, get_current_user(), _instance_product(getattr(self, "instance", None)))

        form_class.__init__ = initialize


def _install_filter_privacy(module):
    # These concrete constructors populate user fields after FilterSet.__init__.
    # Do not materialize generated forms in an unfinished base constructor.
    for name in ("FindingFilter", "AcceptedFindingFilter"):
        filter_class = getattr(module, name)
        original = filter_class.__init__

        def initialize(self, *args, _original=original, **kwargs):
            _original(self, *args, **kwargs)
            actor = get_current_user() or getattr(self, "user", None)
            if is_restricted_user(actor):
                product = _product_for_context(pid=getattr(self, "pid", None), eid=getattr(self, "eid", None), tid=getattr(self, "tid", None))
                _scope_fields(self.form.fields, actor, product)

        filter_class.__init__ = initialize


def _install_serializer_privacy(module):
    seen = set()
    for serializer_class in vars(module).values():
        if not isinstance(serializer_class, type) or serializer_class in seen:
            continue
        if serializer_class.__module__ != module.__name__ or not hasattr(serializer_class, "get_fields"):
            continue
        seen.add(serializer_class)
        original = serializer_class.get_fields

        def fields(self, _original=original):
            result = _original(self)
            request = self.context.get("request")
            actor = getattr(request, "user", None) or get_current_user()
            product = _instance_product(getattr(self, "instance", None)) or request_product(request)
            _scope_fields(result, actor, product)
            return result

        serializer_class.get_fields = fields
    for name in ("UserSerializer", "UserStubSerializer", "DojoGroupSerializer"):
        serializer_class = getattr(module, name)
        original = serializer_class.to_representation

        def represent(self, instance, _original=original, _name=name):
            request = self.context.get("request")
            actor = getattr(request, "user", None) or get_current_user()
            if is_restricted_user(actor):
                ids = visible_user_ids(actor, request_product(request))
                if _name != "DojoGroupSerializer" and instance.pk not in ids:
                    # Prefetched relations require an id; omit names, email, permissions.
                    return {"id": instance.pk}
                result = _original(self, instance)
                if _name == "DojoGroupSerializer" and "users" in result:
                    result["users"] = [pk for pk in result["users"] if pk in ids]
                return result
            return _original(self, instance)

        serializer_class.to_representation = represent


def install_privacy_overlay():
    global _installed
    if _installed:
        return
    from dojo import forms
    from dojo import filters
    from dojo.api_v2 import permissions, serializers
    from dojo.api_v2 import views  # noqa: F401 - import aliases before replacing them
    from dojo.group import queries as group_queries
    from dojo.product import queries as product_queries
    from dojo.product_type import queries as type_queries
    from dojo.user import queries as user_queries

    _install_user_query_wrappers(user_queries)
    _install_member_queries(group_queries, "get_authorized_groups", groups=True)
    for name in ("get_authorized_group_members", "get_authorized_group_members_for_user", "get_group_members_for_group"):
        _install_member_queries(group_queries, name, members=True)
    for module in (product_queries, type_queries):
        for name in ("get_authorized_product_members", "get_authorized_product_members_for_user") if module is product_queries else ("get_authorized_product_type_members", "get_authorized_product_type_members_for_user"):
            _install_member_queries(module, name)
    for module, names in (
        (product_queries, ("get_authorized_members_for_product", "get_authorized_global_members_for_product")),
        (type_queries, ("get_authorized_members_for_product_type", "get_authorized_global_members_for_product_type")),
    ):
        for name in names:
            _install_member_queries(module, name, product_argument=True)

    _install_form_privacy(forms)
    _install_filter_privacy(filters)
    _install_serializer_privacy(serializers)
    original_permission = permissions.UserHasConfigurationPermissionSuperuser.has_permission

    def configuration_permission(self, request, view):
        model = getattr(getattr(view, "queryset", None), "model", None)
        if model is not None and model._meta.concrete_model is get_user_model()._meta.concrete_model and is_restricted_user(request.user):
            return False
        return original_permission(self, request, view)

    permissions.UserHasConfigurationPermissionSuperuser.has_permission = configuration_permission
    original_auto_create = permissions.check_auto_create_permission

    @wraps(original_auto_create)
    def auto_create_permission(user, product, product_name, engagement, engagement_name, product_type, product_type_name, error_message):
        if product is None and is_restricted_user(user):
            if is_shared_restricted_user(user):
                return False
            independent = product_type is not None and (
                Product_Type_Member.objects.filter(user=user, product_type=product_type, role_id__in=PRIVILEGED_ROLES).exists()
                or Product_Type_Group.objects.filter(group__users=user, product_type=product_type, role_id__in=PRIVILEGED_ROLES).exclude(group_id__in=restricted_membership_group_ids(user)).exists()
            )
            if not independent:
                return False
        return original_auto_create(user, product, product_name, engagement, engagement_name, product_type, product_type_name, error_message)

    replace_imported_references(original_auto_create, auto_create_permission)
    permissions.check_auto_create_permission = auto_create_permission
    _installed = True
