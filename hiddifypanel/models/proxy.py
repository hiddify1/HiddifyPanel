from sqlalchemy_serializer import SerializerMixin
from ipaddress import IPv4Address, IPv6Address
from flask import current_app, g, request
from strenum import StrEnum
from enum import auto
import random
import json
import glob
import re
from sqlalchemy import Column, String, Integer, Boolean, Enum, ForeignKey

from hiddifypanel.database import db
from hiddifypanel.cache import cache
from hiddifypanel.models import hconfig, ConfigEnum, Domain, DomainType, get_hconfigs


class ProxyTransport(StrEnum):
    h2 = auto()
    grpc = auto()
    XTLS = auto()
    faketls = auto()
    shadowtls = auto()
    restls1_2 = auto()
    restls1_3 = auto()
    # h1=auto()
    WS = auto()
    tcp = auto()
    ssh = auto()
    httpupgrade = auto()
    custom = auto()
    shadowsocks = auto()


class ProxyCDN(StrEnum):
    CDN = auto()
    direct = auto()
    Fake = auto()
    relay = auto()


class ProxyProto(StrEnum):
    vless = auto()
    trojan = auto()
    vmess = auto()
    ss = auto()
    v2ray = auto()
    ssr = auto()
    ssh = auto()
    tuic = auto()
    hysteria = auto()
    hysteria2 = auto()
    wireguard = auto()


class ProxyL3(StrEnum):
    tls = auto()
    tls_h2 = auto()
    tls_h2_h1 = auto()
    h3_quic = auto()
    reality = auto()
    http = auto()
    kcp = auto()
    ssh = auto()
    udp = auto()
    custom = auto()


class Proxy(db.Model, SerializerMixin):  # type: ignore
    id = Column(Integer, primary_key=True, autoincrement=True)
    child_id = Column(Integer, ForeignKey('child.id'), default=0)
    name = Column(String(200), nullable=False, unique=False)
    enable = Column(Boolean, nullable=False)
    proto = Column(Enum(ProxyProto), nullable=False)
    l3 = Column(Enum(ProxyL3), nullable=False)
    transport = Column(Enum(ProxyTransport), nullable=False)
    cdn = Column(Enum(ProxyCDN), nullable=False)

    @property
    def enabled(self):
        return self.enable * 1

    def to_dict(self):
        return {
            'name': self.name,
            'enable': self.enable,
            'proto': self.proto,
            'l3': self.l3,
            'transport': self.transport,
            'cdn': self.cdn,
            'child_unique_id': self.child.unique_id if self.child else ''
        }

    def __str__(self):
        return str(self.to_dict())

    @staticmethod
    def add_or_update(commit=True, child_id=0, **proxy):
        dbproxy = Proxy.query.filter(Proxy.name == proxy['name']).first()
        if not dbproxy:
            dbproxy = Proxy()
            db.session.add(dbproxy)  # type: ignore
        dbproxy.enable = proxy['enable']
        dbproxy.name = proxy['name']
        dbproxy.proto = proxy['proto']
        dbproxy.transport = proxy['transport']
        dbproxy.cdn = proxy['cdn']
        dbproxy.l3 = proxy['l3']
        dbproxy.child_id = child_id
        if commit:
            db.session.commit()  # type: ignore

    @staticmethod
    def bulk_register(proxies, commit=True, override_child_unique_id=None):
        from hiddifypanel.panel import hiddify
        for proxy in proxies:
            child_id = hiddify.get_child(unique_id=None)
            Proxy.add_or_update(commit=False, child_id=child_id, **proxy)
        if commit:
            db.session.commit()  # type: ignore

    @staticmethod
    @cache.cache(ttl=300)
    def get_proxies(child_id: int = 0, only_enabled=False) -> list['Proxy']:
        proxies = Proxy.query.filter(Proxy.child_id == child_id).all()
        proxies = [c for c in proxies if 'restls' not in c.transport]
        # if not hconfig(ConfigEnum.tuic_enable, child_id):
        #     proxies = [c for c in proxies if c.proto != ProxyProto.tuic]
        # if not hconfig(ConfigEnum.hysteria_enable, child_id):
        #     proxies = [c for c in proxies if c.proto != ProxyProto.hysteria2]
        if not hconfig(ConfigEnum.shadowsocks2022_enable, child_id):
            proxies = [c for c in proxies if 'shadowsocks' != c.transport]

        if not hconfig(ConfigEnum.ssfaketls_enable, child_id):
            proxies = [c for c in proxies if 'faketls' != c.transport]
        if not hconfig(ConfigEnum.v2ray_enable, child_id):
            proxies = [c for c in proxies if 'v2ray' != c.proto]
        if not hconfig(ConfigEnum.shadowtls_enable, child_id):
            proxies = [c for c in proxies if c.transport != 'shadowtls']
        if not hconfig(ConfigEnum.ssr_enable, child_id):
            proxies = [c for c in proxies if 'ssr' != c.proto]
        if not hconfig(ConfigEnum.vmess_enable, child_id):
            proxies = [c for c in proxies if 'vmess' not in c.proto]
        if not hconfig(ConfigEnum.httpupgrade_enable, child_id):
            proxies = [c for c in proxies if ProxyTransport.httpupgrade not in c.transport]
        if not hconfig(ConfigEnum.ws_enable, child_id):
            proxies = [c for c in proxies if ProxyTransport.WS not in c.transport]

        if not hconfig(ConfigEnum.grpc_enable, child_id):
            proxies = [c for c in proxies if ProxyTransport.grpc not in c.transport]
        if not hconfig(ConfigEnum.kcp_enable, child_id):
            proxies = [c for c in proxies if 'kcp' not in c.l3]

        if not hconfig(ConfigEnum.http_proxy_enable, child_id):
            proxies = [c for c in proxies if 'http' != c.l3]

        if not Domain.query.filter(Domain.mode.in_([DomainType.cdn, DomainType.auto_cdn_ip])).first():
            proxies = [c for c in proxies if c.cdn != "CDN"]

        if not Domain.query.filter(Domain.mode.in_([DomainType.relay])).first():
            proxies = [c for c in proxies if c.cdn != ProxyCDN.relay]

        if not Domain.query.filter(Domain.mode.in_([DomainType.cdn, DomainType.auto_cdn_ip]), Domain.servernames != "", Domain.servernames != Domain.domain).first():
            proxies = [c for c in proxies if 'Fake' not in c.cdn]
        proxies = [c for c in proxies if not ('vless' == c.proto and ProxyTransport.tcp == c.transport and c.cdn == ProxyCDN.direct)]

        if only_enabled:
            proxies = [p for p in proxies if p.enable]
        return proxies

    @staticmethod
    def get_valid_proxies(domains: list[Domain]) -> list[dict]:
        from hiddifypanel import hutils

        allp = []
        allphttp = [p for p in request.args.get("phttp", "").split(',') if p]
        allptls = [p for p in request.args.get("ptls", "").split(',') if p]
        added_ip = {}
        configsmap = {}
        proxeismap = {}
        for domain in domains:
            if domain.child_id not in configsmap:
                configsmap[domain.child_id] = get_hconfigs(domain.child_id)
                proxeismap[domain.child_id] = Proxy.get_proxies(domain.child_id, only_enabled=True)
            hconfigs = configsmap[domain.child_id]

            ip = hutils.network.get_domain_ip(domain.domain, version=4)
            ip6 = hutils.network.get_domain_ip(domain.domain, version=6)
            ips = [x for x in [ip, ip6] if x is not None]
            for proxy in proxeismap[domain.child_id]:
                noDomainProxies = False
                if proxy.proto in [ProxyProto.ssh, ProxyProto.wireguard]:
                    noDomainProxies = True
                if proxy.proto in [ProxyProto.ss] and proxy.transport not in [ProxyTransport.grpc, ProxyTransport.h2, ProxyTransport.WS, ProxyTransport.httpupgrade]:
                    noDomainProxies = True
                options = []
                key = f'{proxy.proto}{proxy.transport}{proxy.cdn}{proxy.l3}'
                if key not in added_ip:
                    added_ip[key] = {}
                if proxy.proto in [ProxyProto.ssh, ProxyProto.tuic, ProxyProto.hysteria2, ProxyProto.wireguard, ProxyProto.ss]:
                    if noDomainProxies and all([x in added_ip[key] for x in ips]):
                        continue

                    for x in ips:
                        added_ip[key][x] = 1

                    if proxy.proto in [ProxyProto.ssh, ProxyProto.wireguard, ProxyProto.ss]:
                        if domain.mode == 'fake':
                            continue
                        if proxy.proto in [ProxyProto.ssh]:
                            options = [{'pport': hconfigs[ConfigEnum.ssh_server_port]}]
                        elif proxy.proto in [ProxyProto.wireguard]:
                            options = [{'pport': hconfigs[ConfigEnum.wireguard_port]}]
                        elif proxy.transport in [ProxyTransport.shadowsocks]:
                            options = [{'pport': hconfigs[ConfigEnum.shadowsocks2022_port]}]
                        elif proxy.proto in [ProxyProto.ss]:
                            options = [{'pport': 443}]
                    elif proxy.proto == ProxyProto.tuic:
                        options = [{'pport': hconfigs[ConfigEnum.tuic_port]}]
                    elif proxy.proto == ProxyProto.hysteria2:
                        options = [{'pport': hconfigs[ConfigEnum.hysteria_port]}]
                else:
                    protos = ['http', 'tls'] if hconfigs.get(ConfigEnum.http_proxy_enable) else ['tls']
                    for t in protos:
                        for port in hconfigs[ConfigEnum.http_ports if t == 'http' else ConfigEnum.tls_ports].split(','):
                            phttp = port if t == 'http' else None
                            ptls = port if t == 'tls' else None
                            if phttp and len(allphttp) and phttp not in allphttp:
                                continue
                            if ptls and len(allptls) and ptls not in allptls:
                                continue
                            options.append({'phttp': phttp, 'ptls': ptls})

                for opt in options:
                    pinfo = Proxy.make_proxy(hconfigs, proxy, domain, **opt)
                    if 'msg' not in pinfo:
                        allp.append(pinfo)
        return allp

    @staticmethod
    def make_proxy(hconfigs: dict, proxy: 'Proxy', domain_db: Domain, phttp=80, ptls=443, pport: int | None = None) -> dict:
        from hiddifypanel import hutils

        l3 = proxy.l3
        domain = domain_db.domain
        child_id = domain_db.child_id
        name = proxy.name
        port = hutils.proxy.get_port(proxy, hconfigs, domain_db, ptls, phttp, pport)

        if val_res := hutils.proxy.is_proxy_valid(proxy, domain_db, port):
            # print(val_res)
            return val_res

        if 'reality' in proxy.l3:
            alpn = "h2" if proxy.transport in ['h2', "grpc"] else 'http/1.1'
        else:
            alpn = "h2" if proxy.l3 in ['tls_h2'] or proxy.transport in ["grpc", 'h2'] else 'h2,http/1.1' if proxy.l3 == 'tls_h2_h1' else "http/1.1"
        cdn_forced_host = domain_db.cdn_ip or (domain_db.domain if domain_db.mode != DomainType.reality else hutils.network.get_direct_host_or_ip(4))
        is_cdn = ProxyCDN.CDN == proxy.cdn or ProxyCDN.Fake == proxy.cdn
        base = {
            'name': name,
            'cdn': is_cdn,
            'mode': "CDN" if is_cdn else "direct",
            'l3': l3,
            'host': domain,
            'port': port,
            'server': cdn_forced_host,
            'sni': domain_db.servernames if is_cdn and domain_db.servernames else domain,
            'uuid': str(g.account.uuid),
            'proto': proxy.proto,
            'transport': proxy.transport,
            'proxy_path': hconfigs[ConfigEnum.proxy_path],
            'alpn': alpn,
            'extra_info': f'{domain_db.alias or domain}',
            'fingerprint': hconfigs[ConfigEnum.utls],
            'allow_insecure': domain_db.mode == DomainType.fake or "Fake" in proxy.cdn,
            'dbe': proxy,
            'dbdomain': domain_db
        }
        if proxy.proto in ['tuic', 'hysteria2']:
            base['alpn'] = "h3"
            return base
        if proxy.proto in ['wireguard']:
            base['wg_pub'] = g.account.wg_pub
            base['wg_pk'] = g.account.wg_pk
            base['wg_psk'] = g.account.wg_psk
            base['wg_ipv4'] = hutils.network.add_number_to_ipv4(hconfigs[ConfigEnum.wireguard_ipv4], g.account.id)
            base['wg_ipv6'] = hutils.network.add_number_to_ipv6(hconfigs[ConfigEnum.wireguard_ipv6], g.account.id)
            base['wg_server_pub'] = hconfigs[ConfigEnum.wireguard_public_key]
            base['wg_noise_trick'] = hconfigs[ConfigEnum.wireguard_noise_trick]
            return base

        if proxy.proto in [ProxyProto.vmess]:
            base['cipher'] = "chacha20-poly1305"

        if l3 in ['reality']:
            base['reality_short_id'] = random.sample(hconfigs[ConfigEnum.reality_short_ids].split(','), 1)[0]
            # base['flow']="xtls-rprx-vision"
            base['reality_pbk'] = hconfigs[ConfigEnum.reality_public_key]
            if (domain_db.servernames):
                all_servernames = re.split('[ \t\r\n;,]+', domain_db.servernames)
                base['sni'] = random.sample(all_servernames, 1)[0]
                if hconfigs[ConfigEnum.core_type] == "singbox":
                    base['sni'] = all_servernames[0]
            else:
                base['sni'] = domain_db.domain

            del base['host']
            if base.get('fingerprint'):
                base['fingerprint'] = hconfigs[ConfigEnum.utls]
            # if not domain_db.cdn_ip:
            #     base['server']=hiddify.get_domain_ip(base['server'])

        if "Fake" in proxy.cdn:
            if not hconfigs[ConfigEnum.domain_fronting_domain]:
                return {'name': name, 'msg': "no domain_fronting_domain", 'type': 'debug', 'proto': proxy.proto}
            if l3 == "http" and not hconfigs[ConfigEnum.domain_fronting_http_enable]:
                return {'name': name, 'msg': "no http in domain_fronting_domain", 'type': 'debug', 'proto': proxy.proto}
            if l3 == "tls" and not hconfigs[ConfigEnum.domain_fronting_tls_enable]:
                return {'name': name, 'msg': "no tls in domain_fronting_domain", 'type': 'debug', 'proto': proxy.proto}
            base['server'] = hconfigs[ConfigEnum.domain_fronting_domain]
            base['sni'] = hconfigs[ConfigEnum.domain_fronting_domain]
            # base["host"]=domain
            base['mode'] = 'Fake'
        elif l3 == "http" and not hconfigs[ConfigEnum.http_proxy_enable]:
            return {'name': name, 'msg': "http but http is disabled ", 'type': 'debug', 'proto': proxy.proto}

        path = {
            'vless': f'{hconfigs[ConfigEnum.path_vless]}',
            'trojan': f'{hconfigs[ConfigEnum.path_trojan]}',
            'vmess': f'{hconfigs[ConfigEnum.path_vmess]}',
            'ss': f'{hconfigs[ConfigEnum.path_ss]}',
            'v2ray': f'{hconfigs[ConfigEnum.path_ss]}'
        }

        if base["proto"] in ['v2ray', 'ss', 'ssr']:
            base['cipher'] = hconfigs[ConfigEnum.shadowsocks2022_method]
            base['password'] = f'{hutils.encode.do_base_64(hconfigs[ConfigEnum.shared_secret].replace("-",""))}:{hutils.encode.do_base_64(g.account.uuid.replace("-",""))}'

        if base["proto"] == "ssr":
            base["ssr-obfs"] = "tls1.2_ticket_auth"
            base["ssr-protocol"] = "auth_sha1_v4"
            base["fakedomain"] = hconfigs[ConfigEnum.ssr_fakedomain]
            base["mode"] = "FakeTLS"
            return base
        elif "faketls" in proxy.transport:
            base['fakedomain'] = hconfigs[ConfigEnum.ssfaketls_fakedomain]
            base['mode'] = 'FakeTLS'
            return base
        elif "shadowtls" in proxy.transport:
            base['fakedomain'] = hconfigs[ConfigEnum.shadowtls_fakedomain]
            # base['sni'] = hconfigs[ConfigEnum.shadowtls_fakedomain]
            base['shared_secret'] = hconfigs[ConfigEnum.shared_secret]
            base['mode'] = 'ShadowTLS'
            return base
        elif "shadowsocks" in proxy.transport:
            return base
        if ProxyTransport.XTLS in proxy.transport:
            base['flow'] = 'xtls-rprx-vision'
            return {**base, 'transport': 'tcp'}

        if proxy.proto in {'vless', 'trojan', 'vmess'} and hconfigs.get(ConfigEnum.mux_enable):
            if hconfigs[ConfigEnum.mux_enable]:
                base['mux_enable'] = True
                base['mux_protocol'] = hconfigs[ConfigEnum.mux_protocol]
                base['mux_max_connections'] = hconfigs[ConfigEnum.mux_max_connections]
                base['mux_min_streams'] = hconfigs[ConfigEnum.mux_min_streams]
                base['mux_max_streams'] = hconfigs[ConfigEnum.mux_max_streams]
                base['mux_padding_enable'] = hconfigs[ConfigEnum.mux_padding_enable]

                # the hiddify next client doesn't support mux max streams
                base['mux_max_streams'] = hconfigs[ConfigEnum.mux_max_streams]

                if hconfigs[ConfigEnum.mux_brutal_enable]:
                    base['mux_brutal_enable'] = True
                    base['mux_brutal_up_mbps'] = hconfigs[ConfigEnum.mux_brutal_up_mbps]
                    base['mux_brutal_down_mbps'] = hconfigs[ConfigEnum.mux_brutal_down_mbps]

        if is_cdn and proxy.proto in {'vless', 'trojan', "vmess"}:
            if hconfigs[ConfigEnum.tls_fragment_enable]:
                base["tls_fragment_enable"] = True
                base["tls_fragment_size"] = hconfigs[ConfigEnum.tls_fragment_size]
                base["tls_fragment_sleep"] = hconfigs[ConfigEnum.tls_fragment_sleep]

            if hconfigs[ConfigEnum.tls_mixed_case]:
                base["tls_mixed_case"] = hconfigs[ConfigEnum.tls_mixed_case]

            if hconfigs[ConfigEnum.tls_padding_enable]:
                base["tls_padding_enable"] = hconfigs[ConfigEnum.tls_padding_enable]
                base["tls_padding_length"] = hconfigs[ConfigEnum.tls_padding_length]

        if "tcp" in proxy.transport:
            base['transport'] = 'tcp'
            base['path'] = f'/{path[base["proto"]]}{hconfigs[ConfigEnum.path_tcp]}'
            return base
        if proxy.transport in ["ws", "WS"]:
            base['transport'] = 'ws'
            base['path'] = f'/{path[base["proto"]]}{hconfigs[ConfigEnum.path_ws]}'
            base["host"] = domain
            return base

        if proxy.transport in [ProxyTransport.httpupgrade]:
            base['transport'] = 'httpupgrade'
            base['path'] = f'/{path[base["proto"]]}{hconfigs[ConfigEnum.path_httpupgrade]}'
            base["host"] = domain
            return base

        if proxy.transport == "grpc":
            base['transport'] = 'grpc'
            # base['grpc_mode'] = "multi" if hconfigs[ConfigEnum.core_type]=='xray' else 'gun'
            base['grpc_mode'] = 'gun'
            base['grpc_service_name'] = f'{path[base["proto"]]}{hconfigs[ConfigEnum.path_grpc]}'
            base['path'] = base['grpc_service_name']
            return base

        if "h1" in proxy.transport:
            base['transport'] = 'tcp'
            base['alpn'] = 'http/1.1'
            return base
        if ProxyProto.ssh == proxy.proto:
            base['private_key'] = g.account.ed25519_private_key
            base['host_key'] = hutils.proxy.get_ssh_hostkeys(False)
            # base['ssh_port'] = hconfig(ConfigEnum.ssh_server_port)
            return base
        return {'name': name, 'msg': 'not valid', 'type': 'error', 'proto': proxy.proto}
