// lib/clip_service.dart

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:ui';

import 'package:flutter/widgets.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Configure this with your relay WebSocket URL.
// Examples:
// - ws://YOUR_VPS_IP:8765
// - wss://clipsync-relay.onrender.com
const kRelayUrl = 'wss://clipsync-relay.onrender.com';
const kAppVersion = '0.8.2+13';
const kAuthorName = 'Florentino356';
const kReconnectSteps = [2, 5, 10, 30, 60];
const kHeartbeatInterval = Duration(minutes: 10);

String? cleanId(String raw) {
  final value = raw.replaceAll('-', '').trim();
  return value.length == 9 && int.tryParse(value) != null ? value : null;
}

String? parsePairingCode(String code) {
  final trimmed = code.trim();
  final uri = Uri.tryParse(trimmed);
  if (uri != null && uri.scheme == 'clipsync' && uri.host == 'pair') {
    return cleanId(uri.queryParameters['id'] ?? '');
  }

  try {
    final data = jsonDecode(trimmed);
    if (data is Map) {
      return cleanId('${data['id'] ?? ''}');
    }
  } catch (_) {}

  return cleanId(trimmed);
}

int nextReconnectDelay(int step) {
  return kReconnectSteps[step.clamp(0, kReconnectSteps.length - 1)];
}

void initForegroundTask() {
  FlutterForegroundTask.init(
    androidNotificationOptions: AndroidNotificationOptions(
      channelId: 'clipsync',
      channelName: 'ClipSync',
      channelDescription: 'clipboard sync',
      channelImportance: NotificationChannelImportance.LOW,
      priority: NotificationPriority.LOW,
    ),
    iosNotificationOptions: const IOSNotificationOptions(),
    foregroundTaskOptions: ForegroundTaskOptions(
      eventAction: ForegroundTaskEventAction.repeat(30000),
      autoRunOnBoot: true,
      autoRunOnMyPackageReplaced: true,
      allowWakeLock: true,
    ),
  );
}

@pragma('vm:entry-point')
void taskEntryPoint() {
  DartPluginRegistrant.ensureInitialized();
  WidgetsFlutterBinding.ensureInitialized();
  FlutterForegroundTask.setTaskHandler(ClipTaskHandler());
}

Future<String> getOrCreatePhoneId() async {
  final p = await SharedPreferences.getInstance();
  final id = p.getString('phone_id') ?? '';
  if (id.length == 9 && int.tryParse(id) != null) return id;

  final r = Random();
  final nid = List.generate(9, (_) => r.nextInt(10).toString()).join();
  await p.setString('phone_id', nid);
  return nid;
}

String fmtId(String id) {
  final d = id.replaceAll('-', '');
  if (d.length != 9) return id;
  return '${d.substring(0, 3)}-${d.substring(3, 6)}-${d.substring(6)}';
}

class ClipTaskHandler extends TaskHandler {
  WebSocket? _ws;
  String _targetId = '';
  bool _alive = false;
  bool _connecting = false;
  Timer? _retryTimer;
  int _retryStep = 0;
  int _lastHeartbeatMs = 0;

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    _targetId =
        (await FlutterForegroundTask.getData<String>(key: 'target_id') ?? '')
            .replaceAll('-', '');
    _alive = true;
    _sendDebug('service start target=${fmtId(_targetId)}');
    _connect();
  }

  void _connect() async {
    if (!_alive || _targetId.isEmpty) return;
    if (_connecting) return;

    try {
      _connecting = true;
      await _ws?.close();
      _sendDebug('service connecting $kRelayUrl');
      _ws = await WebSocket.connect(
        kRelayUrl,
      ).timeout(const Duration(seconds: 10));
      _retryStep = 0;

      _ws!.add(jsonEncode({'action': 'subscribe', 'target': _targetId}));
      _lastHeartbeatMs = DateTime.now().millisecondsSinceEpoch;
      _sendDebug('service subscribe sent ${fmtId(_targetId)}');

      _ws!.listen(
        (data) async {
          try {
            final msg = jsonDecode(data as String) as Map<String, dynamic>;
            final type = (msg['type'] ?? msg['status']) as String? ?? '';

            switch (type) {
              case 'subscribed':
                final online = msg['online'] as bool? ?? false;
                _setNotification(
                  online ? 'PC online - ready' : 'Waiting for PC...',
                );
                FlutterForegroundTask.sendDataToMain({
                  'type': 'status',
                  'online': online,
                });
                _sendDebug('service subscribed online=$online');
                break;

              case 'pc_online':
                _setNotification('PC online - ready');
                FlutterForegroundTask.sendDataToMain({
                  'type': 'status',
                  'online': true,
                });
                _sendDebug('service pc_online');
                break;

              case 'pc_offline':
                _setNotification('PC offline');
                FlutterForegroundTask.sendDataToMain({
                  'type': 'status',
                  'online': false,
                });
                _sendDebug('service pc_offline');
                break;

              case 'clip':
                final text = (msg['text'] as String? ?? '').trim();
                if (text.isEmpty) break;

                _sendDebug('service recv clip len=${text.length}');

                FlutterForegroundTask.sendDataToMain({
                  'type': 'clip',
                  'text': text,
                });

                final preview =
                    text.length > 45 ? '${text.substring(0, 45)}...' : text;
                _setNotification('Clipboard: $preview');
                break;

              case 'heartbeat_ack':
                break;
            }
          } catch (e) {
            _sendDebug('service message error: $e');
          }
        },
        onDone: () {
          _sendDebug('service socket done');
          _retry();
        },
        onError: (e) {
          _sendDebug('service socket error: $e');
          _retry();
        },
        cancelOnError: true,
      );
    } catch (e) {
      _setNotification('Connecting...');
      _sendDebug('service connect error: $e');
      _retry();
    } finally {
      _connecting = false;
    }
  }

  void _retry() {
    if (!_alive) return;
    _retryTimer?.cancel();
    final delay = nextReconnectDelay(_retryStep);
    if (_retryStep < kReconnectSteps.length - 1) {
      _retryStep += 1;
    }
    _sendDebug('service reconnect in ${delay}s');
    _retryTimer = Timer(Duration(seconds: delay), _connect);
  }

  void _setNotification(String text) {
    FlutterForegroundTask.updateService(
      notificationTitle: 'ClipSync',
      notificationText: text,
    );
  }

  void _sendDebug(String message) {
    FlutterForegroundTask.sendDataToMain({
      'type': 'debug',
      'message': message,
      'at': DateTime.now().toIso8601String(),
    });
  }

  @override
  void onRepeatEvent(DateTime timestamp) {
    if (_ws == null || _ws!.readyState != WebSocket.open) {
      _connect();
      return;
    }

    final now = DateTime.now().millisecondsSinceEpoch;
    if (now - _lastHeartbeatMs >= kHeartbeatInterval.inMilliseconds) {
      _lastHeartbeatMs = now;
      try {
        _ws?.add(jsonEncode({'action': 'heartbeat', 'role': 'phone'}));
      } catch (e) {
        _sendDebug('service heartbeat error: $e');
        _retry();
      }
    }
  }

  @override
  Future<void> onDestroy(DateTime timestamp) async {
    _alive = false;
    _connecting = false;
    _retryTimer?.cancel();
    await _ws?.close();
  }

  @override
  void onReceiveData(Object data) {}
}
