import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../clip_service.dart';
import 'local_server.dart';
import 'outbox.dart';
import 'slip_ocr.dart';
import 'slip_pipeline.dart';
import 'slip_store.dart';

/// SharedPreferences key for the HMAC secret from QR pairing v2.
const kSharedSecretPrefKey = 'shared_secret';

/// Gate slip capture / local server behind this flag (default off).
const kSlipAutoConfirmPrefKey = 'slip_auto_confirm';

/// Loads the persisted pairing secret, if any.
Future<String?> loadSharedSecret() async {
  final prefs = await SharedPreferences.getInstance();
  final value = prefs.getString(kSharedSecretPrefKey);
  if (value == null || value.isEmpty) {
    return null;
  }
  return value;
}

/// Persists or clears the pairing secret from QR v2.
Future<void> saveSharedSecret(String? secret) async {
  final prefs = await SharedPreferences.getInstance();
  if (secret == null || secret.isEmpty) {
    await prefs.remove(kSharedSecretPrefKey);
    return;
  }
  await prefs.setString(kSharedSecretPrefKey, secret);
}

Future<bool> isSlipAutoConfirmEnabled() async {
  final prefs = await SharedPreferences.getInstance();
  return prefs.getBool(kSlipAutoConfirmPrefKey) ?? false;
}

/// Minimal runtime wiring: watcher → OCR pipeline → outbox + local WS server.
class SlipBootstrap {
  SlipBootstrap({
    required this.targetId,
    required this.sharedSecret,
    this.relayUrl = kRelayUrl,
  });

  final String targetId;
  final String sharedSecret;
  final String relayUrl;

  SlipStore? _store;
  LocalSlipServer? _localServer;
  SlipOutbox? _outbox;
  SlipPipeline? _pipeline;
  StreamSubscription<dynamic>? _pipelineSub;
  WebSocket? _relayWs;
  Timer? _relayRetryTimer;
  int _relayRetryStep = 0;
  bool _running = false;

  bool get isRunning => _running;

  Future<void> start({void Function(String message)? onLog}) async {
    if (_running) {
      return;
    }
    if (targetId.length != 9 || sharedSecret.isEmpty) {
      onLog?.call('Slip: missing target id or shared secret');
      return;
    }

    await Permission.photos.request();

    _store = await SlipStore.open();

    _outbox = SlipOutbox(
      store: _store!,
      sharedSecret: sharedSecret,
      send: _sendRelaySlipEvent,
    );

    _localServer = LocalSlipServer(
      _store!,
      sharedSecret,
      outbox: _outbox,
    );
    await _localServer!.start(port: LocalSlipServer.defaultPort);

    _pipeline = SlipPipeline(
      ocr: MlKitSlipOcr(),
      store: _store!,
      outbox: _outbox,
      outboxForRelay: true,
    );

    _pipelineSub = _pipeline!.watchAndProcess().listen(
      (event) {
        _localServer?.pushSlipEvent(event);
      },
      onError: (Object error) {
        onLog?.call('Slip pipeline error: $error');
      },
    );

    _running = true;
    unawaited(_connectRelay(onLog: onLog));
    onLog?.call('Slip stack started on port ${_localServer!.port}');
  }

  Future<void> stop() async {
    _running = false;
    _relayRetryTimer?.cancel();
    _relayRetryTimer = null;
    await _pipelineSub?.cancel();
    _pipelineSub = null;
    await _relayWs?.close();
    _relayWs = null;
    await _localServer?.stop();
    _localServer = null;
    _pipeline = null;
    _outbox = null;
    _store = null;
  }

  Future<void> _sendRelaySlipEvent(Map<String, dynamic> message) async {
    final ws = _relayWs;
    if (ws == null) {
      return;
    }
    if (message['type'] != 'slip_event') {
      return;
    }
    ws.add(
      jsonEncode({
        'action': 'slip_event',
        'payload': message['payload'],
        'sig': message['sig'],
      }),
    );
  }

  Future<void> _connectRelay({void Function(String message)? onLog}) async {
    if (!_running) {
      return;
    }

    _relayRetryTimer?.cancel();
    await _relayWs?.close();
    _relayWs = null;

    try {
      final ws = await WebSocket.connect(relayUrl).timeout(
        const Duration(seconds: 10),
      );
      _relayWs = ws;
      _relayRetryStep = 0;
      ws.add(jsonEncode({'action': 'subscribe', 'target': targetId}));
      onLog?.call('Slip relay subscribed ${fmtId(targetId)}');

      ws.listen(
        (dynamic data) async {
          try {
            final msg = jsonDecode(data as String) as Map<String, dynamic>;
            final type = msg['type'] as String? ?? '';
            if (type == 'slip_ack') {
              await _outbox?.handleIncoming(msg);
            }
          } catch (_) {
            // Ignore malformed relay frames.
          }
        },
        onDone: () => _scheduleRelayReconnect(onLog: onLog),
        onError: (_) => _scheduleRelayReconnect(onLog: onLog),
        cancelOnError: true,
      );

      await _outbox?.onReconnect(forRelay: true);
    } catch (error) {
      onLog?.call('Slip relay connect error: $error');
      _scheduleRelayReconnect(onLog: onLog);
    }
  }

  void _scheduleRelayReconnect({void Function(String message)? onLog}) {
    if (!_running) {
      return;
    }
    _relayRetryTimer?.cancel();
    final delay = nextReconnectDelay(_relayRetryStep);
    if (_relayRetryStep < kReconnectSteps.length - 1) {
      _relayRetryStep += 1;
    }
    _relayRetryTimer = Timer(Duration(seconds: delay), () {
      unawaited(_connectRelay(onLog: onLog));
    });
  }
}
