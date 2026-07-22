import 'dart:convert';

import 'package:crypto/crypto.dart';

import 'slip_event.dart';
import 'slip_store.dart';

/// Sends a framed slip message over the active transport (USB WS or relay).
typedef SlipSendFn = Future<void> Function(Map<String, dynamic> message);

/// HMAC-SHA256 of canonical JSON payload — matches PC `slip_payload_sig`.
String signSlipPayload(String sharedSecret, Map<String, dynamic> payload) {
  final canonical = _canonicalJson(payload);
  return Hmac(sha256, utf8.encode(sharedSecret))
      .convert(utf8.encode(canonical))
      .toString();
}

String _canonicalJson(Object? value) {
  if (value is Map) {
    final keys = value.keys.map((k) => k.toString()).toList()..sort();
    final parts = <String>[];
    for (final key in keys) {
      parts.add('${jsonEncode(key)}:${_canonicalJson(value[key])}');
    }
    return '{${parts.join(',')}}';
  }
  if (value is List) {
    return '[${value.map(_canonicalJson).join(',')}]';
  }
  return jsonEncode(value);
}

/// Reliable slip delivery: persist unsent → send → ack → markSent; resend on reconnect.
class SlipOutbox {
  SlipOutbox({
    required SlipStore store,
    required SlipSendFn send,
    required this.sharedSecret,
  })  : _store = store,
        _send = send;

  final SlipStore _store;
  final SlipSendFn _send;
  final String sharedSecret;

  /// Persist [event] as unsent (if needed) and push over the injectable transport.
  ///
  /// When [forRelay] is true, attaches HMAC `sig` of the payload (relay path).
  Future<void> enqueue(SlipEvent event, {bool forRelay = false}) async {
    await _store.save(event, sent: false);
    await _send(_buildMessage(event, forRelay: forRelay));
  }

  /// Handle inbound transport messages; `slip_ack` marks the event sent.
  Future<void> handleIncoming(Map<String, dynamic> message) async {
    if (message['type'] != 'slip_ack') {
      return;
    }
    final eventId = message['event_id'];
    if (eventId is! String || eventId.isEmpty) {
      return;
    }
    await _store.markSent(eventId);
  }

  /// Resend every unsent slip after transport reconnect.
  Future<void> onReconnect({bool forRelay = false}) async {
    final pending = await _store.unsent();
    for (final stored in pending) {
      await _send(_buildMessage(stored.toSlipEvent(), forRelay: forRelay));
    }
  }

  Map<String, dynamic> _buildMessage(SlipEvent event, {required bool forRelay}) {
    final payload = event.toJson();
    final message = <String, dynamic>{
      'type': 'slip_event',
      'payload': payload,
    };
    if (forRelay) {
      message['sig'] = signSlipPayload(sharedSecret, payload);
    }
    return message;
  }
}
