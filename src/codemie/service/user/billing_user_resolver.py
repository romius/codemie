# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from fastapi import Depends, Request, status

from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_kb_repository import user_kb_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.settings.settings_request_validator import TEAMS_BOT_INTEGRATION_FEATURE

TEAMS_SERVICE_ACCOUNT_USER_TYPE = "service_account"
TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"  # Header carrying the Teams end-user's email for group-chat


class BillingUserResolver:
    """Resolves the user whose budget should be charged for a Teams-relayed request.

    Only ever swaps identity for the allow-listed Teams service account; every
    other caller gets itself back unchanged. Never affects access control —
    callers must keep using the original caller for authorization checks.
    """

    def resolve(self, caller: User, sender_email: str) -> User:
        if not self._is_allowlisted(caller):
            return caller

        with get_session() as session:
            db_user = self._get_billable_db_user(session, sender_email.lower())
            billing_user = self._to_user(session, db_user)

        logger.info(
            f"billing_user_impersonated: caller_id={caller.id!r} (service account) is billing this request "
            f"to billing_user_id={billing_user.id!r} email={billing_user.email!r}, resolved from "
            f"sender_email={sender_email!r}"
        )

        return billing_user

    @staticmethod
    def _is_allowlisted(caller: User) -> bool:
        if not config.TEAMS_SERVICE_ACCOUNT_ID:
            logger.warning("sender_email_ignored: TEAMS_SERVICE_ACCOUNT_ID is not configured; substitution skipped")
            return False

        if caller.user_type != TEAMS_SERVICE_ACCOUNT_USER_TYPE:
            logger.warning(
                f"sender_email_ignored: caller_id={caller.id!r} has user_type={caller.user_type!r}, "
                f"expected {TEAMS_SERVICE_ACCOUNT_USER_TYPE!r}; substitution skipped"
            )
            return False

        if caller.id != config.TEAMS_SERVICE_ACCOUNT_ID:
            logger.warning(
                f"sender_email_ignored: caller_id={caller.id!r} does not match the configured "
                f"Teams service account id={config.TEAMS_SERVICE_ACCOUNT_ID!r}; substitution skipped"
            )
            return False

        return True

    @staticmethod
    def _get_billable_db_user(session, sender_email: str):
        db_user = user_repository.get_by_email(session, sender_email)

        if db_user is None:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="No user found for sender_email",
                details=f"sender_email={sender_email!r} does not match any known user.",
                help="Verify the Teams end-user's email is registered in CodeMie.",
            )
        if (
            not db_user.is_active
            or db_user.deleted_at is not None
            or db_user.user_type == TEAMS_SERVICE_ACCOUNT_USER_TYPE
        ):
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="No user found for sender_email",
                details=(
                    f"sender_email={sender_email!r} matches a user that is inactive, "
                    "deleted, or a service account, and cannot be used as a billing identity."
                ),
                help="Verify the Teams end-user's email is registered in CodeMie.",
            )

        return db_user

    @staticmethod
    def _to_user(session, db_user) -> User:
        projects = user_project_repository.get_by_user_id(session, db_user.id)
        kbs = user_kb_repository.get_by_user_id(session, db_user.id)

        return User(
            id=db_user.id,
            username=db_user.username,
            name=db_user.name or "",
            email=db_user.email,
            picture=db_user.picture or "",
            user_type=db_user.user_type,
            project_names=[p.project_name for p in projects],
            admin_project_names=[p.project_name for p in projects if p.is_project_admin],
            knowledge_bases=[kb.kb_name for kb in kbs],
            is_admin=db_user.is_admin,
            is_maintainer=db_user.is_maintainer,
            is_auditor=db_user.is_auditor,
            project_limit=db_user.project_limit,
        )


billing_user_resolver = BillingUserResolver()


async def get_billing_user(raw_request: Request, user: User = Depends(authenticate)) -> User:
    """FastAPI dependency: resolve the Teams billing identity for a request.

    Skips the sender_email lookup entirely when the teamsBotIntegration feature
    flag is off, so non-Teams traffic never pays for it. Routes should depend on
    this instead of calling billing_user_resolver directly.
    """
    if not customer_config.is_feature_enabled(TEAMS_BOT_INTEGRATION_FEATURE):
        return user

    sender_email = raw_request.headers.get(TEAMS_SENDER_EMAIL_HEADER)

    if not sender_email:
        return user

    return billing_user_resolver.resolve(user, sender_email)
