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
  bool _running = false;
  bool _pcOnline = false;
  String _lastClip = '';
  String _status = 'Not connected';
  String _targetId = '';
  WebSocket? _uiWs;
  Timer? _uiRetryTimer;
  final List<String> _debugLines = [];

  @override
  void initState() {
    super.initState();
    _loadSaved();
    FlutterForegroundTask.addTaskDataCallback(_onData);
  }

  @override
  void dispose() {
    FlutterForegroundTask.removeTaskDataCallback(_onData);
    _uiRetryTimer?.cancel();
    _uiWs?.close();
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _loadSaved() async {
    final p = await SharedPreferences.getInstance();
    final saved = p.getString('target_id') ?? '';
    final running = await FlutterForegroundTask.isRunningService;
    setState(() {
      _ctrl.text = fmtId(saved);
      _running = running;
      _targetId = saved.replaceAll('-', '');
      if (running && saved.isNotEmpty) {
        _status = 'Running - PC: ${fmtId(saved)}';
      }
    });
    if (running && saved.isNotEmpty) {
      _addDebug('app restored target=${fmtId(saved)}');
      _connectUiSocket(_targetId);
    }
  }

  void _onData(Object data) {
    if (data is! Map) return;
    final msg = Map<String, dynamic>.from(data);
    _addDebug(
      'service ${msg['type']}: ${msg['message'] ?? msg['online'] ?? ''}',
    );
    if (msg['type'] == 'clip') {
      setState(() {
        _pcOnline = true;
        _lastClip = msg['text'] as String? ?? '';
        _status = 'Clipboard received';
      });
    } else if (msg['type'] == 'status') {
      final online = msg['online'] == true;
      setState(() {
        _pcOnline = online;
        _status = online ? 'PC online - ready' : 'Waiting for PC...';
      });
    } else if (msg['type'] == 'debug') {
      setState(() {});
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
    _addDebug('start target=${fmtId(raw)} relay=$kRelayUrl');

    await FlutterForegroundTask.requestNotificationPermission();
    if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
      await FlutterForegroundTask.requestIgnoreBatteryOptimization();
    }

    final result = await FlutterForegroundTask.startService(
      notificationTitle: 'ClipSync',
      notificationText: 'Connecting...',
      callback: taskEntryPoint,
    );
    _addDebug('service start result=$result');

    setState(() {
      _running = true;
      _pcOnline = false;
      _status = 'Connecting...';
    });
    _connectUiSocket(raw);
  }

  Future<void> _stop() async {
    await FlutterForegroundTask.stopService();
    _uiRetryTimer?.cancel();
    await _uiWs?.close();
    _uiWs = null;
    _addDebug('stopped');
    setState(() {
      _running = false;
      _pcOnline = false;
      _status = 'Stopped';
    });
  }

  Future<void> _connectUiSocket(String targetId) async {
    if (targetId.isEmpty) return;

    _uiRetryTimer?.cancel();
    await _uiWs?.close();
    _addDebug('ui connecting');

    try {
      final ws = await WebSocket.connect(
        kRelayUrl,
      ).timeout(const Duration(seconds: 10));
      _uiWs = ws;
      ws.add(jsonEncode({'action': 'subscribe', 'target': targetId}));
      _addDebug('ui subscribe sent ${fmtId(targetId)}');

      ws.listen(
        (data) async {
          try {
            final msg = jsonDecode(data as String) as Map<String, dynamic>;
            final type = (msg['type'] ?? msg['status']) as String? ?? '';
            _addDebug('ui recv $type');

            if (type == 'subscribed') {
              final online = msg['online'] as bool? ?? false;
              if (!mounted) return;
              setState(() {
                _pcOnline = online;
                _status = online ? 'PC online - ready' : 'Waiting for PC...';
              });
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
              _addDebug('ui copied len=${text.length}');
            }
          } catch (e) {
            _addDebug('ui message error: $e');
          }
        },
        onDone: () {
          _addDebug('ui socket done');
          _scheduleUiReconnect();
        },
        onError: (Object e) {
          _addDebug('ui socket error: $e');
          _scheduleUiReconnect();
        },
        cancelOnError: true,
      );
    } catch (e) {
      _addDebug('ui connect error: $e');
      _scheduleUiReconnect();
    }
  }

  void _scheduleUiReconnect() {
    if (!_running || _targetId.isEmpty) return;
    _uiRetryTimer?.cancel();
    _uiRetryTimer = Timer(const Duration(seconds: 5), () {
      _connectUiSocket(_targetId);
    });
  }

  void _addDebug(String line) {
    final now = DateTime.now();
    final stamp =
        '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}:${now.second.toString().padLeft(2, '0')}';
    if (!mounted) {
      _debugLines.insert(0, '$stamp $line');
      return;
    }
    setState(() {
      _debugLines.insert(0, '$stamp $line');
      if (_debugLines.length > 8) {
        _debugLines.removeRange(8, _debugLines.length);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: 12),
              Row(
                children: [
                  const Text(
                    'ClipSync',
                    style: TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    'v$kAppVersion',
                    style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                  ),
                  const Spacer(),
                  Container(
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: (_running && _pcOnline)
                          ? Colors.green
                          : _running
                          ? Colors.orange
                          : Colors.grey.shade400,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    (_running && _pcOnline)
                        ? 'Online'
                        : _running
                        ? 'Connecting'
                        : 'Offline',
                    style: TextStyle(
                      fontSize: 13,
                      color: (_running && _pcOnline)
                          ? Colors.green
                          : Colors.grey,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                _status,
                style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 32),
              Text(
                'PC ID',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
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
                  fontSize: 28,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 4,
                ),
                decoration: InputDecoration(
                  hintText: 'XXX-XXX-XXX',
                  hintStyle: TextStyle(
                    fontSize: 28,
                    fontWeight: FontWeight.w300,
                    letterSpacing: 4,
                    color: cs.onSurfaceVariant.withOpacity(0.3),
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
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                height: 54,
                child: FilledButton(
                  onPressed: _running ? _stop : _start,
                  style: FilledButton.styleFrom(
                    backgroundColor: _running
                        ? Colors.red.shade400
                        : cs.primary,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                  ),
                  child: Text(
                    _running ? 'Stop Sync' : 'Start Sync',
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
              if (_running) ...[
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                  decoration: BoxDecoration(
                    color: cs.primaryContainer.withOpacity(0.4),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        Icons.info_outline_rounded,
                        size: 15,
                        color: cs.primary,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Running in background. You can turn the screen off.',
                          style: TextStyle(fontSize: 12, color: cs.primary),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 12),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: cs.surfaceContainerHighest.withOpacity(0.7),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Debug',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: cs.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'Relay: $kRelayUrl',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 11,
                        color: cs.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 6),
                    for (final line in _debugLines)
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
              const Spacer(),
              if (_lastClip.isNotEmpty) ...[
                Text(
                  'Last clipboard',
                  style: TextStyle(
                    fontSize: 12,
                    color: cs.onSurfaceVariant,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 6),
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
                const SizedBox(height: 4),
                Text(
                  'Tap to copy again',
                  style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
                ),
              ],
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }
}
