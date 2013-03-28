import uuid
import json

from keystone import config
from keystone import exception
from keystone import identity
from keystone.common import controller
from keystone.common import dependency
from keystone.common import logging
from keystone import exception
from keystone.openstack.common import timeutils


LOG = logging.getLogger(__name__)
CONF = config.CONF


def _trustor_only(context, trust, user_id):
    if user_id != trust.get('trustor_user_id'):
        raise exception.Forbidden()


def _admin_trustor_trustee_only(context, trust, user_id):
    if (user_id != trust.get('trustor_user_id') and
            user_id != trust.get('trustor_user_id') and
            context['is_admin']):
                raise exception.Forbidden()


def _admin_trustor_only(context, trust, user_id):
    if user_id != trust.get('trustor_user_id') and not context['is_admin']:
        raise exception.Forbidden()


@dependency.requires('identity_api', 'trust_api', 'token_api')
class TrustV3(controller.V3Controller):
    collection_name = "trusts"
    member_name = "trust"

    def _get_user_id(self, context):
        if 'token_id' in context:
            token_id = context['token_id']
            token = self.token_api.get_token(context, token_id)
            user_id = token['user']['id']
            return user_id
        return None

    def get_trust(self, context, trust_id):
        user_id = self._get_user_id(context)
        trust = self.trust_api.get_trust(context, trust_id)
        if not trust:
            raise exception.TrustNotFound(trust_id)
        _admin_trustor_trustee_only(context, trust, user_id)
        if not trust:
            raise exception.TrustNotFound(trust_id=trust_id)
        if (user_id != trust['trustor_user_id'] and
                user_id != trust['trustee_user_id']):
            raise exception.Forbidden()
        self._fill_in_roles(context, trust,
                            self.identity_api.list_roles(context))
        return TrustV3.wrap_member(context, trust)

    def _fill_in_roles(self, context, trust, global_roles):
        if trust.get('expires_at') is not None:
            trust['expires_at'] = (timeutils.isotime
                                   (trust['expires_at'],
                                    subsecond=True))

        if not 'roles' in trust:
            trust['roles'] = []
        trust_full_roles = []
        for trust_role in trust['roles']:
            if isinstance(trust_role, basestring):
                trust_role = {'id': trust_role}
            matching_roles = [x for x in global_roles
                              if x['id'] == trust_role['id']]
            if matching_roles:
                full_role = identity.controllers.RoleV3.wrap_member(
                    context, matching_roles[0])['role']
                trust_full_roles.append(full_role)
        trust['roles'] = trust_full_roles
        trust['roles_links'] = {
            'self': (CONF.public_endpoint % CONF +
                     "trusts/%s/roles" % trust['id']),
            'next': None,
            'previous': None}

    def _clean_role_list(self, context, trust, global_roles):
        trust_roles = []
        global_role_names = dict((r['name'], r)
                                 for r in
                                 global_roles)
        for role in trust.get('roles', []):
            if 'id' in role:
                trust_roles.append({'id': role['id']})
            elif 'name' in role:
                rolename = role['name']
                if rolename in global_role_names:
                    trust_roles.append({'id':
                                        global_role_names[rolename]['id']})
                else:
                    raise exception.RoleNotFound("role %s is not defined" %
                                                 rolename)
            else:
                raise exception.ValidationError(attribute='id or name',
                                                target='roles')
        return trust_roles

    @controller.protected
    def create_trust(self, context, trust=None):
        """
        The user creating the trust must be trustor
        """

        #TODO instead of raising  ValidationError on the first problem,
        #return a collection of all the problems.
        if not trust:
            raise exception.ValidationError(attribute='trust',
                                            target='request')
        try:
            user_id = self._get_user_id(context)
            _trustor_only(context, trust, user_id)
            #confirm that the trustee exists
            trustee_ref = self.identity_api.get_user(context,
                                                     trust['trustee_user_id'])
            if not trustee_ref:
                raise exception.UserNotFound(user_id=trust['trustee_user_id'])
            global_roles = self.identity_api.list_roles(context)
            clean_roles = self._clean_role_list(context, trust, global_roles)
            if trust.get('project_id'):
                user_roles = self.identity_api.get_roles_for_user_and_project(
                    context, user_id, trust['project_id'])
            else:
                user_roles = []
            for trust_role in clean_roles:
                matching_roles = [x for x in user_roles
                                  if x == trust_role['id']]
                if not matching_roles:
                    raise exception.RoleNotFound(role_id=trust_role['id'])
            if trust.get('expires_at') is not None:
                if not trust['expires_at'].endswith('Z'):
                    trust['expires_at'] += 'Z'
                trust['expires_at'] = (timeutils.parse_isotime
                                       (trust['expires_at']))
            new_trust = self.trust_api.create_trust(
                context=context,
                trust_id=uuid.uuid4().hex,
                trust=trust,
                roles=clean_roles)
            self._fill_in_roles(context,
                                new_trust,
                                global_roles)
            return TrustV3.wrap_member(context, new_trust)
        except KeyError as e:
            raise exception.ValidationError(attribute=e.args[0],
                                            target='trust')

    @controller.protected
    def list_trusts(self, context):
        query = context['query_string']
        trusts = []
        if not query:
            self.assert_admin(context)
            trusts += self.trust_api.list_trusts(context)
        if 'trustor_user_id' in query:
            user_id = query['trustor_user_id']
            calling_user_id = self._get_user_id(context)
            if user_id != calling_user_id:
                raise exception.Forbidden()
            trusts += (self.trust_api.
                       list_trusts_for_trustor(context, user_id))
        if 'trustee_user_id' in query:
            user_id = query['trustee_user_id']
            calling_user_id = self._get_user_id(context)
            if user_id != calling_user_id:
                raise exception.Forbidden()
            trusts += (self.trust_api.
                       list_trusts_for_trustee(context, user_id))
        global_roles = self.identity_api.list_roles(context)
        for trust in trusts:
            self._fill_in_roles(context, trust, global_roles)
        return TrustV3.wrap_collection(context, trusts)

    @controller.protected
    def delete_trust(self, context, trust_id):
        trust = self.trust_api.get_trust(context, trust_id)
        if not trust:
            raise exception.TrustNotFound(trust_id)

        user_id = self._get_user_id(context)
        _admin_trustor_only(context, trust, user_id)
        self.trust_api.delete_trust(context, trust_id)
        userid = trust['trustor_user_id']
        token_list = self.token_api.list_tokens(context,
                                                userid,
                                                trust_id=trust_id)
        for token in token_list:
            self.token_api.delete_token(context, token)

    @controller.protected
    def list_roles_for_trust(self, context, trust_id):
        trust = self.get_trust(context, trust_id)['trust']
        if not trust:
            raise exception.TrustNotFound(trust_id)
        user_id = self._get_user_id(context)
        _admin_trustor_trustee_only(context, trust, user_id)
        return {'roles': trust['roles'],
                'links': trust['roles_links']}

    @controller.protected
    def check_role_for_trust(self, context, trust_id, role_id):
        """Checks if a role has been assigned to a trust."""
        trust = self.trust_api.get_trust(context, trust_id)
        if not trust:
            raise exception.TrustNotFound(trust_id)
        user_id = self._get_user_id(context)
        _admin_trustor_trustee_only(context, trust, user_id)
        matching_roles = [x for x in trust['roles']
                          if x['id'] == role_id]
        if not matching_roles:
            raise exception.RoleNotFound(role_id=role_id)

    @controller.protected
    def get_role_for_trust(self, context, trust_id, role_id):
        """Checks if a role has been assigned to a trust."""
        trust = self.trust_api.get_trust(context, trust_id)
        if not trust:
            raise exception.TrustNotFound(trust_id)

        user_id = self._get_user_id(context)
        _admin_trustor_trustee_only(context, trust, user_id)
        matching_roles = [x for x in trust['roles']
                          if x['id'] == role_id]
        if not matching_roles:
            raise exception.RoleNotFound(role_id=role_id)
        global_roles = self.identity_api.list_roles(context)
        matching_roles = [x for x in global_roles
                          if x['id'] == role_id]
        if matching_roles:
            full_role = (identity.controllers.
                         RoleV3.wrap_member(context, matching_roles[0]))
            return full_role
        else:
            raise exception.RoleNotFound(role_id=role_id)
