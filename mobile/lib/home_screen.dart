// lib/home_screen.dart

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

  @override
  void initState() {
    super.initState();
    _loadSaved();
    FlutterForegroundTask.addTaskDataCallback(_onData);
  }

  @override
  void dispose() {
    FlutterForegroundTask.removeTaskDataCallback(_onData);
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
      if (running && saved.isNotEmpty) {
        _status = 'Running - PC: ${fmtId(saved)}';
      }
    });
  }

  void _onData(Object data) {
    if (data is! Map) return;
    final msg = Map<String, dynamic>.from(data);
    if (msg['type'] == 'clip') {
      setState(() {
        _pcOnline = true;
        _lastClip = msg['text'] as String? ?? '';
        _status = 'Clipboard received';
      });
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

    await FlutterForegroundTask.requestNotificationPermission();
    if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
      await FlutterForegroundTask.requestIgnoreBatteryOptimization();
    }

    await FlutterForegroundTask.startService(
      notificationTitle: 'ClipSync',
      notificationText: 'Connecting...',
      callback: taskEntryPoint,
    );

    setState(() {
      _running = true;
      _pcOnline = false;
      _status = 'Connecting...';
    });
  }

  Future<void> _stop() async {
    await FlutterForegroundTask.stopService();
    setState(() {
      _running = false;
      _pcOnline = false;
      _status = 'Stopped';
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
                    backgroundColor: _running ? Colors.red.shade400 : cs.primary,
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
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(
                    color: cs.primaryContainer.withOpacity(0.4),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    children: [
                      Icon(Icons.info_outline_rounded,
                          size: 15, color: cs.primary),
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
