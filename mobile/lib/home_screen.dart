// lib/home_screen.dart

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'clip_service.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _ctrl = TextEditingController();
  final List<String> _events = [];
  WebSocket? _fallbackWs;
  Timer? _fallbackRetryTimer;
  bool _running = false;
  bool _pcOnline = false;
  bool _fallbackActive = false;
  bool _showDiagnostics = false;
  String _lastClip = '';
  String _status = 'Not connected';
  String _targetId = '';

  @override
  void initState() {
    super.initState();
    _loadSaved();
    FlutterForegroundTask.addTaskDataCallback(_onData);
  }

  @override
  void dispose() {
    FlutterForegroundTask.removeTaskDataCallback(_onData);
    _fallbackRetryTimer?.cancel();
    _fallbackWs?.close();
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _loadSaved() async {
    final p = await SharedPreferences.getInstance();
    final saved = p.getString('target_id') ?? '';
    final running = await FlutterForegroundTask.isRunningService;
    setState(() {
      _ctrl.text = fmtId(saved);
      _targetId = saved.replaceAll('-', '');
      _running = running;
      if (running && saved.isNotEmpty) {
        _status = 'Sync running';
      }
    });
    if (running && saved.isNotEmpty) {
      _addEvent('Restored ${fmtId(saved)}');
    }
  }

  void _onData(Object data) {
    if (data is! Map) return;
    final msg = Map<String, dynamic>.from(data);
    final type = msg['type'] as String? ?? '';

    if (type == 'clip') {
      final text = msg['text'] as String? ?? '';
      setState(() {
        _pcOnline = true;
        _lastClip = text;
        _status = 'Clipboard received';
      });
      _addEvent('Clipboard ${text.length} chars');
    } else if (type == 'status') {
      final online = msg['online'] == true;
      setState(() {
        _pcOnline = online;
        _status = online ? 'PC online - ready' : 'Waiting for PC';
      });
      _addEvent(online ? 'PC online' : 'PC offline');
    } else if (type == 'debug') {
      final message = msg['message'] as String? ?? '';
      if (message.isNotEmpty) _addEvent(message);
    }
  }

  void _onChanged(String val) {
    final digits = val.replaceAll('-', '');
    if (digits.length > 9) return;

    final buf = StringBuffer();
    for (var i = 0; i < digits.length; i++) {
      if (i == 3 || i == 6) buf.write('-');
      buf.write(digits[i]);
    }
    final formatted = buf.toString();

    if (formatted != val) {
      _ctrl.value = TextEditingValue(
        text: formatted,
        selection: TextSelection.collapsed(offset: formatted.length),
      );
    }
  }

  Future<void> _start() async {
    final raw = _ctrl.text.replaceAll('-', '').trim();

    if (raw.length != 9 || int.tryParse(raw) == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('ID must be 9 digits, e.g. 847-293-156')),
      );
      return;
    }

    final p = await SharedPreferences.getInstance();
    await p.setString('target_id', raw);
    await FlutterForegroundTask.saveData(key: 'target_id', value: raw);
    _targetId = raw;
    _addEvent('Start ${fmtId(raw)}');

    await FlutterForegroundTask.requestNotificationPermission();
    if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
      await FlutterForegroundTask.requestIgnoreBatteryOptimization();
    }

    final result = await _startOrRestartService();
    final error = _serviceResultError(result);
    if (error == null) {
      _addEvent('Service started');
      await _stopFallbackSocket();
    } else {
      _addEvent('Service failed: $error');
      _addEvent('Fallback app socket enabled');
    }

    setState(() {
      _running = true;
      _pcOnline = false;
      _fallbackActive = error != null;
      _status = error == null ? 'Connecting...' : 'App sync active';
    });

    if (error != null) {
      _connectFallbackSocket(raw);
    }
  }

  Future<void> _stop() async {
    await FlutterForegroundTask.stopService();
    await _stopFallbackSocket();
    _addEvent('Stopped');
    setState(() {
      _running = false;
      _pcOnline = false;
      _fallbackActive = false;
      _status = 'Stopped';
    });
  }

  Future<ServiceRequestResult> _startOrRestartService() async {
    if (await FlutterForegroundTask.isRunningService) {
      return FlutterForegroundTask.restartService();
    }

    return FlutterForegroundTask.startService(
      notificationTitle: 'ClipSync',
      notificationText: 'Connecting...',
      callback: taskEntryPoint,
    );
  }

  String? _serviceResultError(ServiceRequestResult result) {
    if (result is ServiceRequestFailure) {
      return result.error.toString();
    }
    return null;
  }

  Future<void> _connectFallbackSocket(String targetId) async {
    if (targetId.isEmpty || !_running) return;

    _fallbackRetryTimer?.cancel();
    await _fallbackWs?.close();
    _addEvent('Fallback connecting');

    try {
      final ws = await WebSocket.connect(
        kRelayUrl,
      ).timeout(const Duration(seconds: 10));
      _fallbackWs = ws;
      ws.add(jsonEncode({'action': 'subscribe', 'target': targetId}));
      _addEvent('Fallback subscribe ${fmtId(targetId)}');

      ws.listen(
        (data) async {
          try {
            final msg = jsonDecode(data as String) as Map<String, dynamic>;
            final type = (msg['type'] ?? msg['status']) as String? ?? '';

            if (type == 'subscribed') {
              final online = msg['online'] as bool? ?? false;
              if (!mounted) return;
              setState(() {
                _pcOnline = online;
                _status = online ? 'PC online - ready' : 'Waiting for PC';
              });
              _addEvent('Fallback subscribed online=$online');
            } else if (type == 'pc_online') {
              if (!mounted) return;
              setState(() {
                _pcOnline = true;
                _status = 'PC online - ready';
              });
            } else if (type == 'pc_offline') {
              if (!mounted) return;
              setState(() {
                _pcOnline = false;
                _status = 'PC offline';
              });
            } else if (type == 'clip') {
              final text = msg['text'] as String? ?? '';
              if (text.isEmpty) return;
              await Clipboard.setData(ClipboardData(text: text));
              if (!mounted) return;
              setState(() {
                _pcOnline = true;
                _lastClip = text;
                _status = 'Clipboard received';
              });
              _addEvent('Fallback copied ${text.length} chars');
            }
          } catch (e) {
            _addEvent('Fallback message error: $e');
          }
        },
        onDone: () {
          _addEvent('Fallback socket closed');
          _scheduleFallbackReconnect();
        },
        onError: (Object e) {
          _addEvent('Fallback socket error: $e');
          _scheduleFallbackReconnect();
        },
        cancelOnError: true,
      );
    } catch (e) {
      _addEvent('Fallback connect error: $e');
      _scheduleFallbackReconnect();
    }
  }

  void _scheduleFallbackReconnect() {
    if (!_running || !_fallbackActive || _targetId.isEmpty) return;
    _fallbackRetryTimer?.cancel();
    _fallbackRetryTimer = Timer(const Duration(seconds: 5), () {
      _connectFallbackSocket(_targetId);
    });
  }

  Future<void> _stopFallbackSocket() async {
    _fallbackRetryTimer?.cancel();
    _fallbackRetryTimer = null;
    _fallbackActive = false;
    await _fallbackWs?.close();
    _fallbackWs = null;
  }

  void _addEvent(String line) {
    final now = DateTime.now();
    final stamp =
        '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}:${now.second.toString().padLeft(2, '0')}';
    if (!mounted) {
      _events.insert(0, '$stamp $line');
      return;
    }
    setState(() {
      _events.insert(0, '$stamp $line');
      if (_events.length > 8) {
        _events.removeRange(8, _events.length);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final statusColor = (_running && _pcOnline)
        ? const Color(0xFF19A94B)
        : _running
            ? const Color(0xFFE09C18)
            : cs.outline;

    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 42,
                    height: 42,
                    decoration: BoxDecoration(
                      color: cs.primary,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Icon(
                      Icons.content_paste_go_rounded,
                      color: cs.onPrimary,
                      size: 25,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const Text(
                              'ClipSync',
                              style: TextStyle(
                                fontSize: 25,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            const SizedBox(width: 8),
                            Text(
                              'v$kAppVersion',
                              style: TextStyle(
                                fontSize: 12,
                                color: cs.onSurfaceVariant,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 2),
                        Text(
                          'By $kAuthorName',
                          style: TextStyle(
                            fontSize: 12,
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 9,
                      vertical: 6,
                    ),
                    decoration: BoxDecoration(
                      color: statusColor.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(18),
                    ),
                    child: Row(
                      children: [
                        Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            color: statusColor,
                            shape: BoxShape.circle,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          (_running && _pcOnline)
                              ? 'Online'
                              : _running
                                  ? 'Syncing'
                                  : 'Offline',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: statusColor,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Text(
                _status,
                style: TextStyle(fontSize: 14, color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 30),
              Text(
                'PC ID',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: cs.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _ctrl,
                enabled: !_running,
                onChanged: _onChanged,
                keyboardType: TextInputType.number,
                style: const TextStyle(
                  fontSize: 27,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 4,
                ),
                decoration: InputDecoration(
                  hintText: 'XXX-XXX-XXX',
                  hintStyle: TextStyle(
                    fontSize: 27,
                    fontWeight: FontWeight.w300,
                    letterSpacing: 4,
                    color: cs.onSurfaceVariant.withValues(alpha: 0.3),
                  ),
                  filled: true,
                  fillColor: cs.surfaceContainerHighest,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8),
                    borderSide: BorderSide.none,
                  ),
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 18,
                    vertical: 18,
                  ),
                ),
              ),
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                height: 54,
                child: FilledButton.icon(
                  onPressed: _running ? _stop : _start,
                  icon: Icon(
                    _running ? Icons.stop_rounded : Icons.sync_rounded,
                  ),
                  label: Text(
                    _running ? 'Stop Sync' : 'Start Sync',
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  style: FilledButton.styleFrom(
                    backgroundColor:
                        _running ? const Color(0xFFE34337) : cs.primary,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                  ),
                ),
              ),
              if (_running) ...[
                const SizedBox(height: 12),
                _InfoStrip(
                  icon: _fallbackActive
                      ? Icons.phone_android_rounded
                      : Icons.bolt_rounded,
                  text: _fallbackActive
                      ? 'App sync active. Keep this screen open.'
                      : 'Background sync active',
                  color: _fallbackActive ? const Color(0xFFE09C18) : cs.primary,
                ),
              ],
              const SizedBox(height: 28),
              if (_lastClip.isNotEmpty) ...[
                Text(
                  'Last clipboard',
                  style: TextStyle(
                    fontSize: 12,
                    color: cs.onSurfaceVariant,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 8),
                GestureDetector(
                  onTap: () async {
                    await Clipboard.setData(ClipboardData(text: _lastClip));
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text('Copied'),
                          duration: Duration(seconds: 1),
                        ),
                      );
                    }
                  },
                  child: Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: cs.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      _lastClip,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 14, height: 1.5),
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 18),
              TextButton.icon(
                onPressed: () {
                  setState(() {
                    _showDiagnostics = !_showDiagnostics;
                  });
                },
                icon: Icon(
                  _showDiagnostics
                      ? Icons.expand_less_rounded
                      : Icons.expand_more_rounded,
                ),
                label: const Text('Diagnostics'),
              ),
              if (_showDiagnostics) ...[
                const SizedBox(height: 6),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: cs.surfaceContainerHighest.withValues(alpha: 0.72),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Relay: $kRelayUrl',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 11,
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                      if (_targetId.isNotEmpty)
                        Text(
                          'Target: ${fmtId(_targetId)}',
                          style: TextStyle(
                            fontSize: 11,
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                      Text(
                        'Mode: ${_fallbackActive ? 'app socket fallback' : 'foreground service'}',
                        style: TextStyle(
                          fontSize: 11,
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                      const SizedBox(height: 7),
                      for (final line in _events)
                        Text(
                          line,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 11,
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _InfoStrip extends StatelessWidget {
  const _InfoStrip({
    required this.icon,
    required this.text,
    required this.color,
  });

  final IconData icon;
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 10),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.09),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(icon, size: 17, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: color,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
