from urllib.parse import urlparse
from flask_restful import Resource
from hiddifypanel.models.config import StrConfig, hconfig
from hiddifypanel.models.config_enum import ConfigEnum
from hiddifypanel.models.domain import DomainType
from hiddifypanel.models.user import days_to_reset, user_by_uuid
from hiddifypanel.panel import hiddify
from hiddifypanel.panel.database import db

from flask_classful import FlaskView, route
from flask.views import MethodView

from flask import current_app as app
from flask import g, request
from apiflask import APIFlask, Schema, abort
from apiflask.fields import Integer, String, Float, URL,Dict
from apiflask.validators import Length, OneOf

from hiddifypanel.panel.commercial.restapi.v2.user.DTO import *
from hiddifypanel.panel.user import link_maker
from hiddifypanel.panel.user.user import get_common_data






class InfoAPI(MethodView):
    decorators = [hiddify.user_auth]

    @app.output(ProfileDTO)
    def get(self):
        c = get_common_data(g.user_uuid, 'new')

        dto = ProfileDTO()
        dto.profile_title = c['profile_title']
        dto.profile_url = f"https://{urlparse(request.base_url).hostname}/{g.proxy_path}/{g.user_uuid}/#{g.user.name}"
        dto.profile_usage_current = g.user.current_usage_GB
        dto.profile_usage_total = g.user.usage_limit_GB
        dto.profile_remaining_days = g.user.remaining_days
        dto.profile_reset_days = days_to_reset(g.user)
        dto.telegram_bot_url = f"https://t.me/{c['bot'].username}?start={g.user_uuid}" if c['bot'] else ""
        dto.telegram_id = c['user']['telegram_id']
        dto.admin_message_html =  hconfig(ConfigEnum.branding_freetext)
        dto.admin_message_url = hconfig(ConfigEnum.branding_site)
        dto.brand_title = hconfig(ConfigEnum.branding_title)
        dto.brand_icon_url = ""
        dto.doh = f"https://{urlparse(request.base_url).hostname}/{g.proxy_path}/dns/dns-query"
        dto.lang = hconfig(ConfigEnum.lang)#TODO: change to user
        return dto

    @app.input(UserInfoChangableDTO,arg_name='data')
    def patch(self,data):
        # TODO Need to be test
        
        if data['telegram_id']:
            user = user_by_uuid(g.user_uuid)
            user.telegram_id = data['telegram_id']
            db.session.commit()
            
        if data['language']: #TODO: change to user
            StrConfig.query.filter(StrConfig.key == ConfigEnum.lang).update({'value': data['language']})
            db.session.commit()

            

        abort(200,"Ok")
        # return {'message': 'ok'}


class MTProxiesAPI(MethodView):
    decorators = [hiddify.user_auth]
    @app.output(MtproxyDTO(many=True))
    def get(self):
        # get domains
        c = get_common_data(g.user_uuid, 'new')
        dtos = []
        # TODO: Remove duplicated domains mapped to a same ipv4 and v6
        for d in c['domains']:
            if d.mode not in [DomainType.direct, DomainType.relay]:
                continue
            hexuuid = hconfig(ConfigEnum.shared_secret, d.child_id).replace('-', '')
            telegram_faketls_domain_hex = hconfig(ConfigEnum.telegram_fakedomain, d.child_id).encode('utf-8').hex()
            server_link = f'tg://proxy?server={d.domain}&port=443&secret=ee{hexuuid}{telegram_faketls_domain_hex}'
            dto = MtproxyDTO()
            dto.title = d.alias or d.domain
            dto.link = server_link
            dtos.append(dto)
        return dtos



class AllConfigsAPI(MethodView):
    decorators = [hiddify.user_auth]

    @app.output(ConfigDTO(many=True))
    def get(self):
        def create_item(name, type, domain, protocol, transport, security, link):
            dto = ConfigDTO()
            dto.name = name
            dto.type = type
            dto.domain = domain
            dto.protocol = protocol
            dto.transport = transport
            dto.security = security
            dto.link = link
            return dto

        items = []
        base_url = f"https://{urlparse(request.base_url).hostname}/{g.proxy_path}/{g.user_uuid}/"
        c = get_common_data(g.user_uuid, 'new')

        # Add Auto
        items.append(
            create_item(
                "Auto", "All", "All", "All", "All", "All",
                f"{base_url}sub/?asn={c['asn']}")
        )

        # Add Full Singbox
        items.append(
            create_item(
                "Full Singbox", "All", "All", "All", "All", "All",
                f"{base_url}full-singbox.json?asn={c['asn']}"
            )
        )

        # Add Clash Meta
        items.append(
            create_item(
                "Clash Meta", "All", "All", "All", "All", "All",
                f"clashmeta://install-config?url={base_url}clash/meta/all.yml&name=mnormal_{c['db_domain'].alias or c['db_domain'].domain}-{c['asn']}-{c['mode']}&asn={c['asn']}&mode={c['mode']}"
            )
        )

        # Add Clash
        items.append(
            create_item(
                "Clash", "All", "All", "Except VLess", "All", "All",
                f"clash://install-config?url={base_url}clash/all.yml&name=new_normal_{c['db_domain'].alias or c['db_domain'].domain}-{c['asn']}-{c['mode']}&asn={c['asn']}&mode={c['mode']}"
            )
        )

        # Add Singbox: SSh
        if hconfig(ConfigEnum.ssh_server_enable):
            items.append(
                create_item(
                    "Singbox: SSH", "SSH", "SSH", "SSH", "SSH", "SSH",
                    f"{base_url}singbox.json?name={c['db_domain'].alias or c['db_domain'].domain}-{c['asn']}&asn={c['asn']}&mode={c['mode']}"
                )
            )

        # Add Subscription link
        items.append(
            create_item(
                "Subscription link", "All", "All", "All", "All", "All",
                f"{base_url}all.txt?name={c['db_domain'].alias or c['db_domain'].domain}-{c['asn']}&asn={c['asn']}&mode={c['mode']}"
            )
        )

        # Add Subscription link base64
        items.append(
            create_item(
                "Subscription link b64", "All", "All", "All", "All", "All",
                f"{base_url}all.txt?name=new_link_{c['db_domain'].alias or c['db_domain'].domain}-{c['asn']}-{c['mode']}&asn={c['asn']}&mode={c['mode']}&base64=True"
            )
        )

        for pinfo in link_maker.get_all_validated_proxies(c['domains']):
            items.append(
                create_item(
                    pinfo["name"].replace("_", " "),
                    f"{'Auto ' if pinfo['dbdomain'].has_auto_ip else ''}{pinfo['mode']}",
                    pinfo['server'],
                    pinfo['proto'],
                    pinfo['transport'],
                    pinfo['l3'],
                    f"{link_maker.to_link(pinfo)}"
                )
            )

        return items


class ShortAPI(MethodView):
    decorators = [hiddify.user_auth]

    @app.output(ShortDTO)
    def get(self):
        short = hiddify.add_short_link(
            "/"+hconfig(ConfigEnum.proxy_path)+"/"+g.user.uuid+"/")
        full_url = f"https://{urlparse(request.base_url).hostname}/{short}"
        dto = ShortDTO()
        dto.full_url = full_url
        dto.short = short
        return dto