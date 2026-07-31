import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../async_guard.dart';
import '../clip_service.dart';
import '../relay_failover.dart';
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
    this.relayUrls = kRelayUrls,
  });

  final String targetId;
  final String sharedSecret;
  final List<String> relayUrls;

  SlipStore? _store;
  LocalSlipServer? _localServer;
  SlipOutbox? _outbox;
  SlipPipeline? _pipeline;
  StreamSubscription<dynamic>? _pipelineSub;
  WebSocket? _relayWs;
  Timer? _relayRetryTimer;
  late final RelaySelector _relaySelector = RelaySelector(relayUrls);
  int _relayRetryStep = 0;
  bool _relayReconnectScheduled = false;
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
      send: _sendRelayMessage,
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
    _relayReconnectScheduled = false;
    _relayRetryStep = 0;
    _relaySelector.reset();

    final pipelineSub = _pipelineSub;
    _pipelineSub = null;
    await awaitGuardedVoid(
      pipelineSub?.cancel(),
      timeout: const Duration(seconds: 2),
    );

    final relayWs = _relayWs;
    _relayWs = null;
    await awaitGuardedVoid(
      relayWs?.close(),
      timeout: const Duration(seconds: 2),
    );

    final localServer = _localServer;
    _localServer = null;
    await awaitGuardedVoid(
      localServer?.stop(),
      timeout: const Duration(seconds: 3),
    );

    final pipeline = _pipeline;
    _pipeline = null;
    final ocr = pipeline?.ocr;
    if (ocr is MlKitSlipOcr) {
      await awaitGuardedVoid(
        ocr.close(),
        timeout: const Duration(seconds: 2),
      );
    }

    _outbox = null;
    _store = null;
  }

  Future<void> _sendRelayMessage(Map<String, dynamic> message) async {
    final ws = _relayWs;
    if (ws == null) {
      return;
    }
    final type = message['type'];
    if (type != 'slip_event' && type != 'image_response') {
      return;
    }
    final outgoing = <String, dynamic>{
      'action': type,
      ...message,
    }..remove('type');
    ws.add(jsonEncode(outgoing));
  }

  Future<void> _connectRelay({void Function(String message)? onLog}) async {
    if (!_running) {
      return;
    }

    _relayRetryTimer?.cancel();
    _relayRetryTimer = null;
    await _relayWs?.close();
    _relayWs = null;

    try {
      final url = _relaySelector.current;
      final ws = await WebSocket.connect(url).timeout(
        const Duration(seconds: 10),
      );
      if (!_running) {
        await awaitGuardedVoid(
          ws.close(),
          timeout: const Duration(seconds: 2),
        );
        return;
      }
      _relayWs = ws;
      _relayRetryStep = 0;
      _relayReconnectScheduled = false;
      onLog?.call('Slip relay connected $url');
      ws.add(jsonEncode({'action': 'subscribe', 'target': targetId}));
      onLog?.call('Slip relay subscribed ${fmtId(targetId)}');

      ws.listen(
        (dynamic data) async {
          try {
            final msg = jsonDecode(data as String) as Map<String, dynamic>;
            final type = msg['type'] as String? ?? '';
            if (type == 'slip_ack' || type == 'image_request') {
              await _outbox?.handleIncoming(msg);
            } else if (type == 'pc_online') {
              // The phone WS can stay connected while the PC reconnects.
              // Re-drive the durable queue when Relay announces PC online.
              await _outbox?.onReconnect(forRelay: true);
            }
          } catch (_) {
            // Ignore malformed relay frames.
          }
        },
        onDone: () => _handleRelayFailure(onLog: onLog),
        onError: (_) => _handleRelayFailure(onLog: onLog),
        cancelOnError: true,
      );

      await _outbox?.onReconnect(forRelay: true);
    } catch (error) {
      onLog?.call('Slip relay connect error: $error');
      _handleRelayFailure(onLog: onLog);
    }
  }

  void _handleRelayFailure({void Function(String message)? onLog}) {
    if (!_running || _relayReconnectScheduled) {
      return;
    }
    _relaySelector.failed();
    _scheduleRelayReconnect(onLog: onLog);
  }

  void _scheduleRelayReconnect({void Function(String message)? onLog}) {
    if (!_running) {
      return;
    }
    if (_relayReconnectScheduled) {
      return;
    }
    _relayReconnectScheduled = true;
    _relayRetryTimer?.cancel();
    final delay = nextReconnectDelay(_relayRetryStep);
    if (_relayRetryStep < kReconnectSteps.length - 1) {
      _relayRetryStep += 1;
    }
    _relayRetryTimer = Timer(Duration(seconds: delay), () {
      _relayReconnectScheduled = false;
      unawaited(_connectRelay(onLog: onLog));
    });
  }
}
