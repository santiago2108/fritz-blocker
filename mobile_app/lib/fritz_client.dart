// Minimal TR-064 SOAP client for talking to a FritzBox router directly,
// mirroring the subset of behavior used by the Python `fritzconnection`
// library (Hosts1 + X_AVM-DE_HostFilter1 services, HTTP Digest auth).
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';

class FritzAuthException implements Exception {
  final String message;
  FritzAuthException(this.message);
  @override
  String toString() => message;
}

class FritzSoapException implements Exception {
  final String errorCode;
  final String detail;
  FritzSoapException(this.errorCode, this.detail);
  @override
  String toString() => 'FritzBox error $errorCode';
}

class FritzHost {
  final String ip;
  final String mac;
  final String name;
  final bool active;

  const FritzHost({
    required this.ip,
    required this.mac,
    required this.name,
    required this.active,
  });
}

const _hostsService = (
  type: 'urn:dslforum-org:service:Hosts:1',
  controlPath: '/upnp/control/hosts',
);
const _filterService = (
  type: 'urn:dslforum-org:service:X_AVM-DE_HostFilter:1',
  controlPath: '/upnp/control/x_hostfilter',
);

class FritzTr064Client {
  final String host;
  final int port;
  final String user;
  final String password;
  final HttpClient _client = HttpClient();

  _DigestChallenge? _challenge;
  int _nc = 0;

  FritzTr064Client({
    required this.host,
    required this.user,
    required this.password,
    this.port = 49000,
  }) {
    _client.connectionTimeout = const Duration(seconds: 8);
  }

  void close() => _client.close(force: true);

  String _buildAuthHeader(Uri uri, String method) {
    final c = _challenge!;
    _nc++;
    final ncStr = _nc.toRadixString(16).padLeft(8, '0');
    final cnonce = _randomHex(8);
    final ha1 = md5.convert(utf8.encode('$user:${c.realm}:$password')).toString();
    final ha2 = md5.convert(utf8.encode('$method:${uri.path}')).toString();

    String response;
    String extra;
    if (c.qop != null) {
      response = md5
          .convert(utf8.encode('$ha1:${c.nonce}:$ncStr:$cnonce:${c.qop}:$ha2'))
          .toString();
      extra = ', qop=${c.qop}, nc=$ncStr, cnonce="$cnonce"';
    } else {
      response = md5.convert(utf8.encode('$ha1:${c.nonce}:$ha2')).toString();
      extra = '';
    }
    final opaquePart = c.opaque != null ? ', opaque="${c.opaque}"' : '';
    return 'Digest username="$user", realm="${c.realm}", nonce="${c.nonce}", '
        'uri="${uri.path}", response="$response"$extra$opaquePart, algorithm=MD5';
  }

  Future<String> _call(
    ({String type, String controlPath}) service,
    String action,
    Map<String, String> args,
  ) async {
    final uri = Uri.parse('http://$host:$port${service.controlPath}');
    final argsXml =
        args.entries.map((e) => '<${e.key}>${e.value}</${e.key}>').join();
    final body = '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
        'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        '<s:Body><u:$action xmlns:u="${service.type}">$argsXml</u:$action></s:Body>'
        '</s:Envelope>';
    final bodyBytes = utf8.encode(body);

    Future<HttpClientResponse> attempt(String? authHeader) async {
      final req = await _client.postUrl(uri);
      req.headers.set('soapaction', '${service.type}#$action');
      req.headers.set('content-type', 'text/xml');
      req.headers.set('charset', 'utf-8');
      if (authHeader != null) {
        req.headers.set(HttpHeaders.authorizationHeader, authHeader);
      }
      req.contentLength = bodyBytes.length;
      req.add(bodyBytes);
      return req.close();
    }

    var res = await attempt(_challenge != null ? _buildAuthHeader(uri, 'POST') : null);

    if (res.statusCode == 401) {
      final rawHeaders = res.headers[HttpHeaders.wwwAuthenticateHeader];
      await res.drain();
      if (rawHeaders == null || rawHeaders.isEmpty) {
        throw FritzAuthException('Router did not present an auth challenge.');
      }
      final headerLine = rawHeaders.firstWhere(
        (h) => h.toLowerCase().contains('digest'),
        orElse: () => rawHeaders.first,
      );
      _challenge = _DigestChallenge.parse(headerLine);
      _nc = 0;
      res = await attempt(_buildAuthHeader(uri, 'POST'));
    }

    final respBody = await res.transform(utf8.decoder).join();

    if (res.statusCode == 401) {
      throw FritzAuthException('Login failed. Check the router password.');
    }
    if (res.statusCode != 200) {
      final errorCode = _extractTag(respBody, 'errorCode') ?? 'unknown';
      throw FritzSoapException(errorCode, respBody);
    }
    return respBody;
  }

  Future<List<FritzHost>> getHostsInfo() async {
    final hosts = <FritzHost>[];
    for (var index = 0; ; index++) {
      String xml;
      try {
        xml = await _call(_hostsService, 'GetGenericHostEntry', {'NewIndex': '$index'});
      } on FritzSoapException catch (e) {
        if (e.errorCode == '713') break; // AVM's "no more entries" code
        rethrow;
      }
      final ip = _extractTag(xml, 'NewIPAddress') ?? '';
      if (ip.isEmpty) continue;
      hosts.add(FritzHost(
        ip: ip,
        mac: _extractTag(xml, 'NewMACAddress') ?? '',
        name: _extractTag(xml, 'NewHostName') ?? ip,
        active: _extractTag(xml, 'NewActive') == '1',
      ));
    }
    return hosts;
  }

  Future<bool> isBlocked(String ip) async {
    final xml = await _call(_filterService, 'GetWANAccessByIP', {'NewIPv4Address': ip});
    return _extractTag(xml, 'NewDisallow') == '1';
  }

  Future<void> setBlocked(String ip, bool block) async {
    await _call(_filterService, 'DisallowWANAccessByIP', {
      'NewIPv4Address': ip,
      'NewDisallow': block ? '1' : '0',
    });
  }
}

String? _extractTag(String xmlStr, String tag) {
  final match = RegExp('<$tag>([\\s\\S]*?)</$tag>').firstMatch(xmlStr);
  return match?.group(1);
}

String _randomHex(int bytes) {
  final rnd = Random.secure();
  return List.generate(bytes, (_) => rnd.nextInt(256).toRadixString(16).padLeft(2, '0'))
      .join();
}

class _DigestChallenge {
  final String realm;
  final String nonce;
  final String? qop;
  final String? opaque;

  const _DigestChallenge({
    required this.realm,
    required this.nonce,
    this.qop,
    this.opaque,
  });

  static _DigestChallenge parse(String header) {
    String? extract(String key) =>
        RegExp('$key="?([^",]*)"?').firstMatch(header)?.group(1);
    return _DigestChallenge(
      realm: extract('realm') ?? '',
      nonce: extract('nonce') ?? '',
      qop: extract('qop'),
      opaque: extract('opaque'),
    );
  }
}
