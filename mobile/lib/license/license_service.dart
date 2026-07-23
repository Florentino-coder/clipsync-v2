/// Offline-first Ed25519 license token issue and verify (mobile port of PC).
///
/// The embedded [kPublicKeyRaw] is a **development/test** keypair public half.
/// Replace it with the production public key before shipping.
library;

import 'dart:convert';

import 'package:android_id/android_id.dart';
import 'package:crypto/crypto.dart';
import 'package:ed25519_edwards/ed25519_edwards.dart' as ed;
import 'package:flutter/foundation.dart';

const int kGraceDays = 3;
const int kYellowDaysThreshold = 5;

/// DEV/TEST ONLY — replace with production Ed25519 public key (32 raw bytes).
final Uint8List kPublicKeyRaw = Uint8List.fromList([
  0x99, 0x03, 0xb6, 0xef, 0xfe, 0x3c, 0x85, 0xd1, 0xab, 0xb1, 0xc1, 0xe5, 0x71,
  0x8f, 0x63, 0x5a, 0xe6, 0xac, 0x8a, 0x4a, 0xba, 0x5a, 0x57, 0x1a, 0xaa, 0x00,
  0xa0, 0x1f, 0xba, 0x47, 0x7f, 0xdb,
]);

@immutable
class VerifyResult {
  const VerifyResult({
    required this.valid,
    this.daysLeft,
    this.warning,
    this.reason,
    this.customer,
    this.expiresAt,
  });

  final bool valid;
  final int? daysLeft;
  final String? warning;
  final String? reason;
  final String? customer;
  final DateTime? expiresAt;
}

String _b64encode(List<int> data) {
  return base64Url.encode(data).replaceAll('=', '');
}

Uint8List _b64decode(String data) {
  final pad = '=' * ((4 - data.length % 4) % 4);
  return Uint8List.fromList(base64Url.decode('$data$pad'));
}

DateTime _ensureAware(DateTime dt) {
  if (dt.isUtc) return dt;
  return dt.toUtc();
}

DateTime _parseIso(String value) {
  final normalized = value.replaceAll('Z', '+00:00');
  return _ensureAware(DateTime.parse(normalized));
}

/// Compact JSON with sorted keys — must match PC `json.dumps(..., sort_keys=True)`.
Uint8List encodeCanonicalJson(Map<String, Object?> payload) {
  final keys = payload.keys.toList()..sort();
  final buffer = StringBuffer('{');
  for (var i = 0; i < keys.length; i++) {
    if (i > 0) buffer.write(',');
    final key = keys[i];
    buffer.write(jsonEncode(key));
    buffer.write(':');
    buffer.write(jsonEncode(payload[key]));
  }
  buffer.write('}');
  return Uint8List.fromList(utf8.encode(buffer.toString()));
}

ed.PublicKey _publicKeyFrom(Object? publicKey) {
  if (publicKey == null) {
    return ed.PublicKey(kPublicKeyRaw);
  }
  if (publicKey is ed.PublicKey) {
    return publicKey;
  }
  if (publicKey is Uint8List) {
    return ed.PublicKey(publicKey);
  }
  if (publicKey is List<int>) {
    return ed.PublicKey(Uint8List.fromList(publicKey));
  }
  throw ArgumentError('publicKey must be PublicKey or bytes');
}

/// sha256(androidId) hex truncated to 16 chars — mirrors PC device_id binding.
String deviceIdFromAndroidId(String androidId) {
  return sha256.convert(utf8.encode(androidId)).toString().substring(0, 16);
}

/// Resolve device id from Android ID when available; [androidId] injects for tests.
Future<String> getDeviceId({
  String? androidId,
  AndroidId? androidIdPlugin,
}) async {
  if (androidId != null) {
    return deviceIdFromAndroidId(androidId);
  }
  try {
    final plugin = androidIdPlugin ?? const AndroidId();
    final id = await plugin.getId();
    if (id != null && id.trim().isNotEmpty) {
      return deviceIdFromAndroidId(id.trim());
    }
  } catch (_) {
    // Platform channel unavailable (tests / non-Android).
  }
  return deviceIdFromAndroidId('fallback:unknown');
}

String issueToken(
  ed.PrivateKey privateKey, {
  required String deviceId,
  required String customer,
  required int days,
  DateTime? issuedAt,
}) {
  if (days < 1) {
    throw ArgumentError('days must be >= 1');
  }
  final issued = _ensureAware(issuedAt ?? DateTime.now().toUtc());
  final expires = issued.add(Duration(days: days));
  final payload = <String, Object>{
    'customer': customer,
    'device_id': deviceId,
    'expires_at': expires.toIso8601String(),
    'issued_at': issued.toIso8601String(),
  };
  final payloadBytes = encodeCanonicalJson(payload);
  final signature = ed.sign(privateKey, payloadBytes);
  return '${_b64encode(payloadBytes)}.${_b64encode(signature)}';
}

VerifyResult verifyToken(
  String token, {
  String? deviceId,
  DateTime? now,
  Object? publicKey,
}) {
  // deviceId must be injected by callers that already resolved getDeviceId().
  final expectedDevice = deviceId ?? 'fallback:unknown';
  final checkNow = _ensureAware(now ?? DateTime.now().toUtc());
  final pub = _publicKeyFrom(publicKey);

  late final Uint8List payloadBytes;
  late final Uint8List signature;
  try {
    final parts = token.split('.');
    if (parts.length != 2) {
      return const VerifyResult(valid: false, reason: 'bad_signature');
    }
    payloadBytes = _b64decode(parts[0]);
    signature = _b64decode(parts[1]);
  } catch (_) {
    return const VerifyResult(valid: false, reason: 'bad_signature');
  }

  try {
    if (!ed.verify(pub, payloadBytes, signature)) {
      return const VerifyResult(valid: false, reason: 'bad_signature');
    }
  } catch (_) {
    return const VerifyResult(valid: false, reason: 'bad_signature');
  }

  late final String tokenDevice;
  late final String customer;
  late final DateTime expiresAt;
  try {
    final payload = jsonDecode(utf8.decode(payloadBytes));
    if (payload is! Map) {
      return const VerifyResult(valid: false, reason: 'bad_signature');
    }
    tokenDevice = '${payload['device_id']}';
    customer = '${payload['customer']}';
    expiresAt = _parseIso('${payload['expires_at']}');
  } catch (_) {
    return const VerifyResult(valid: false, reason: 'bad_signature');
  }

  if (tokenDevice != expectedDevice) {
    return const VerifyResult(valid: false, reason: 'device_mismatch');
  }

  final daysLeft = expiresAt.difference(checkNow).inDays;

  if (!checkNow.isAfter(expiresAt)) {
    return VerifyResult(
      valid: true,
      daysLeft: daysLeft,
      customer: customer,
      expiresAt: expiresAt,
    );
  }

  final graceDeadline = expiresAt.add(const Duration(days: kGraceDays));
  if (!checkNow.isAfter(graceDeadline)) {
    return VerifyResult(
      valid: true,
      daysLeft: daysLeft,
      warning: 'License expired; within grace period',
      customer: customer,
      expiresAt: expiresAt,
    );
  }

  return VerifyResult(
    valid: false,
    reason: 'expired',
    daysLeft: daysLeft,
    customer: customer,
    expiresAt: expiresAt,
  );
}

/// PC-aligned badge: green / yellow (≤5 days) / red (locked).
String licenseBadge({required bool allowed, int? daysLeft}) {
  if (!allowed) return 'red';
  if (daysLeft != null && daysLeft <= kYellowDaysThreshold) return 'yellow';
  return 'green';
}

String licenseBadgeLabel(VerifyResult result) {
  if (!result.valid) {
    if (result.reason == 'missing_token') {
      return 'ยังไม่มี license';
    }
    if (result.reason == 'device_mismatch') {
      return 'เครื่องไม่ตรงกับ token';
    }
    if (result.reason == 'expired') {
      return 'หมดอายุ — ล็อก';
    }
    return 'ล็อก — ใส่ token';
  }
  final days = result.daysLeft ?? 0;
  if (days <= kYellowDaysThreshold) {
    return 'เหลือ ≤$kYellowDaysThreshold วัน';
  }
  return 'ใช้งานได้อีก $days วัน';
}
