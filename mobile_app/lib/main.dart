import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

void main() => runApp(const FritzBlockerApp());

class FritzBlockerApp extends StatelessWidget {
  const FritzBlockerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FritzBox Blocker',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFFC0392B)),
        useMaterial3: true,
      ),
      home: const LoginPage(),
    );
  }
}

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

class ApiClient {
  final String baseUrl;
  final String cookie;

  const ApiClient({required this.baseUrl, required this.cookie});

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'Cookie': cookie,
      };

  Future<List<Device>> fetchDevices() async {
    final res = await http
        .get(Uri.parse('$baseUrl/api/devices'), headers: _headers)
        .timeout(const Duration(seconds: 10));
    if (res.statusCode == 401) throw const AuthException();
    if (res.statusCode != 200) throw Exception('Server error ${res.statusCode}');
    final list = jsonDecode(res.body) as List;
    return list.map((j) => Device.fromJson(j as Map<String, dynamic>)).toList();
  }

  Future<void> setBlocked(List<String> ips, {required bool block}) async {
    final res = await http
        .post(
          Uri.parse('$baseUrl/api/block'),
          headers: _headers,
          body: jsonEncode({'ips': ips, 'block': block}),
        )
        .timeout(const Duration(seconds: 10));
    if (res.statusCode == 401) throw const AuthException();
    if (res.statusCode != 200) throw Exception('Server error ${res.statusCode}');
  }
}

class AuthException implements Exception {
  const AuthException();
}

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

class Device {
  final String name;
  final String ip;
  final String mac;
  final bool active;
  final bool blocked;

  const Device({
    required this.name,
    required this.ip,
    required this.mac,
    required this.active,
    required this.blocked,
  });

  factory Device.fromJson(Map<String, dynamic> j) => Device(
        name: (j['name'] as String?) ?? j['ip'] as String,
        ip: j['ip'] as String,
        mac: (j['mac'] as String?) ?? '',
        active: (j['active'] as bool?) ?? false,
        blocked: (j['blocked'] as bool?) ?? false,
      );

  bool get isAmazon => name.toLowerCase().contains('amazon');
}

// ---------------------------------------------------------------------------
// Login page
// ---------------------------------------------------------------------------

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _serverCtrl = TextEditingController();
  final _passCtrl = TextEditingController();
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadSavedServer();
  }

  Future<void> _loadSavedServer() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString('server_url');
    if (saved != null) setState(() => _serverCtrl.text = saved);
  }

  Future<void> _login() async {
    final url = _serverCtrl.text.trim().replaceAll(RegExp(r'/$'), '');
    final password = _passCtrl.text;
    if (url.isEmpty || password.isEmpty) {
      setState(() => _error = 'Enter server URL and password.');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final res = await http.post(
        Uri.parse('$url/login'),
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: {'password': password},
      ).timeout(const Duration(seconds: 10));

      final landed = res.request?.url.path ?? '';
      final setCookie = res.headers['set-cookie'] ?? '';
      if (!landed.contains('devices') || setCookie.isEmpty) {
        setState(() => _error = 'Login failed. Check the password.');
        return;
      }

      final cookie = setCookie.split(';').first;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('server_url', url);

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (_) => DevicesPage(
            client: ApiClient(baseUrl: url, cookie: cookie),
            serverUrl: url,
          ),
        ),
      );
    } catch (e) {
      setState(() => _error = 'Could not reach server. Check the URL.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'FritzBox Blocker',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 32),
              TextField(
                controller: _serverCtrl,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'Server URL',
                  hintText: 'http://192.168.x.x:5000',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _passCtrl,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'Router admin password',
                  border: OutlineInputBorder(),
                ),
                onSubmitted: (_) => _login(),
              ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: const TextStyle(color: Color(0xFFC0392B))),
              ],
              const SizedBox(height: 20),
              FilledButton(
                onPressed: _loading ? null : _login,
                child: _loading
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: Colors.white),
                      )
                    : const Text('Log In'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Devices page
// ---------------------------------------------------------------------------

class DevicesPage extends StatefulWidget {
  final ApiClient client;
  final String serverUrl;

  const DevicesPage({super.key, required this.client, required this.serverUrl});

  @override
  State<DevicesPage> createState() => _DevicesPageState();
}

class _DevicesPageState extends State<DevicesPage> {
  List<Device> _devices = [];
  final Set<String> _selected = {};
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final devices = await widget.client.fetchDevices();
      if (mounted) setState(() => _devices = devices);
    } on AuthException {
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const LoginPage()),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _toggle(Device dev) async {
    try {
      await widget.client.setBlocked([dev.ip], block: !dev.blocked);
      await _load();
    } on AuthException {
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const LoginPage()),
        );
      }
    } catch (e) {
      if (mounted) _showError(e.toString());
    }
  }

  Future<void> _batchSet({required bool block}) async {
    if (_selected.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Select at least one device first.')),
      );
      return;
    }
    try {
      await widget.client.setBlocked(_selected.toList(), block: block);
      setState(() => _selected.clear());
      await _load();
    } on AuthException {
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const LoginPage()),
        );
      }
    } catch (e) {
      if (mounted) _showError(e.toString());
    }
  }

  Future<void> _toggleAmazon() async {
    final amazonIps =
        _devices.where((d) => d.isAmazon).map((d) => d.ip).toList();
    if (amazonIps.isEmpty) return;
    final allBlocked =
        _devices.where((d) => d.isAmazon).every((d) => d.blocked);
    try {
      await widget.client.setBlocked(amazonIps, block: !allBlocked);
      await _load();
    } on AuthException {
      if (mounted) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const LoginPage()),
        );
      }
    } catch (e) {
      if (mounted) _showError(e.toString());
    }
  }

  void _showError(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
          content: Text(msg), backgroundColor: const Color(0xFFC0392B)),
    );
  }

  List<Device> get _amazonDevices =>
      _devices.where((d) => d.isAmazon).toList();
  bool get _allAmazonBlocked =>
      _amazonDevices.isNotEmpty && _amazonDevices.every((d) => d.blocked);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Devices'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : _load,
          ),
          PopupMenuButton<String>(
            onSelected: (v) async {
              if (v == 'logout') {
                final navigator = Navigator.of(context);
                await http.post(
                  Uri.parse('${widget.serverUrl}/logout'),
                  headers: {'Cookie': widget.client.cookie},
                );
                if (!mounted) return;
                navigator.pushReplacement(
                  MaterialPageRoute(builder: (_) => const LoginPage()),
                );
              }
            },
            itemBuilder: (_) => [
              const PopupMenuItem(value: 'logout', child: Text('Log out')),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          // Amazon quick-block bar
          if (_amazonDevices.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton(
                  style: FilledButton.styleFrom(
                    backgroundColor: _allAmazonBlocked
                        ? const Color(0xFF27AE60)
                        : const Color(0xFFC0392B),
                  ),
                  onPressed: _loading ? null : _toggleAmazon,
                  child: Text(
                    '${_allAmazonBlocked ? 'Unblock' : 'Block'} All Amazon Devices (${_amazonDevices.length})',
                  ),
                ),
              ),
            ),

          // Device list
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(_error!,
                                style: const TextStyle(
                                    color: Color(0xFFC0392B))),
                            const SizedBox(height: 12),
                            FilledButton(
                                onPressed: _load,
                                child: const Text('Retry')),
                          ],
                        ),
                      )
                    : RefreshIndicator(
                        onRefresh: _load,
                        child: ListView.separated(
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          itemCount: _devices.length,
                          separatorBuilder: (context2, i2) =>
                              const Divider(height: 1),
                          itemBuilder: (_, i) {
                            final dev = _devices[i];
                            final selected = _selected.contains(dev.ip);
                            return ListTile(
                              leading: Checkbox(
                                value: selected,
                                onChanged: (v) => setState(() {
                                  v!
                                      ? _selected.add(dev.ip)
                                      : _selected.remove(dev.ip);
                                }),
                              ),
                              title: Text(dev.name),
                              subtitle: Text(
                                '${dev.ip}  •  ${dev.active ? 'online' : 'offline'}',
                                style: const TextStyle(fontSize: 12),
                              ),
                              trailing: FilledButton(
                                style: FilledButton.styleFrom(
                                  backgroundColor: dev.blocked
                                      ? const Color(0xFF27AE60)
                                      : const Color(0xFFC0392B),
                                  minimumSize: const Size(88, 36),
                                ),
                                onPressed: () => _toggle(dev),
                                child: Text(
                                    dev.blocked ? 'Unblock' : 'Block'),
                              ),
                            );
                          },
                        ),
                      ),
          ),

          // Batch action bar
          if (!_loading && _error == null)
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => setState(() {
                          if (_selected.length == _devices.length) {
                            _selected.clear();
                          } else {
                            _selected
                                .addAll(_devices.map((d) => d.ip));
                          }
                        }),
                        child: Text(
                          _selected.length == _devices.length
                              ? 'Clear All'
                              : 'Select All',
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: FilledButton(
                        style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFF27AE60)),
                        onPressed: () => _batchSet(block: false),
                        child: const Text('Unblock'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: FilledButton(
                        style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFFC0392B)),
                        onPressed: () => _batchSet(block: true),
                        child: const Text('Block'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
