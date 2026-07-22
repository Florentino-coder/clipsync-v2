import 'dart:convert';
import 'dart:typed_data';

import 'package:clipsync_app/license/license_gate.dart';
import 'package:clipsync_app/license/license_service.dart';
import 'package:ed25519_edwards/ed25519_edwards.dart' as ed;
import 'package:flutter_test/flutter_test.dart';

DateTime _utcnow() => DateTime.utc(2026, 7, 22, 12, 0, 0);

(ed.PrivateKey, ed.PublicKey) _keypair() {
  final pair = ed.generateKey();
  return (pair.privateKey, pair.publicKey);
}

void main() {
  group('license verify', () {
    test('valid token', () {
      final (privateKey, publicKey) = _keypair();
      final now = _utcnow();
      final token = issueToken(
        privateKey,
        deviceId: 'dev_abc123',
        customer: 'cust_001',
        days: 30,
        issuedAt: now,
      );

      final result = verifyToken(
        token,
        deviceId: 'dev_abc123',
        now: now,
        publicKey: publicKey,
      );

      expect(result.valid, isTrue);
      expect(result.daysLeft, 30);
      expect(result.reason, isNull);
      expect(result.warning, isNull);
    });

    test('expired within grace', () {
      final (privateKey, publicKey) = _keypair();
      final now = _utcnow();
      final issuedAt = now.subtract(const Duration(days: 10));
      final token = issueToken(
        privateKey,
        deviceId: 'dev_abc123',
        customer: 'cust_001',
        days: 8,
        issuedAt: issuedAt,
      );
      expect(kGraceDays, 3);

      final result = verifyToken(
        token,
        deviceId: 'dev_abc123',
        now: now,
        publicKey: publicKey,
      );

      expect(result.valid, isTrue);
      expect(result.warning, isNotNull);
      expect(result.warning!.toLowerCase(), contains('grace'));
    });

    test('expired beyond grace', () {
      final (privateKey, publicKey) = _keypair();
      final now = _utcnow();
      final issuedAt = now.subtract(const Duration(days: 20));
      final token = issueToken(
        privateKey,
        deviceId: 'dev_abc123',
        customer: 'cust_001',
        days: 10,
        issuedAt: issuedAt,
      );

      final result = verifyToken(
        token,
        deviceId: 'dev_abc123',
        now: now,
        publicKey: publicKey,
      );

      expect(result.valid, isFalse);
      expect(result.reason, 'expired');
    });

    test('tampered payload', () {
      final (privateKey, publicKey) = _keypair();
      final now = _utcnow();
      final token = issueToken(
        privateKey,
        deviceId: 'dev_abc123',
        customer: 'cust_001',
        days: 30,
        issuedAt: now,
      );
      final parts = token.split('.');
      final payloadB64 = parts[0];
      final signatureB64 = parts[1];
      final chars = payloadB64.split('');
      chars[0] = chars[0] == 'A' ? 'B' : 'A';
      final tampered = '${chars.join()}.$signatureB64';

      final result = verifyToken(
        tampered,
        deviceId: 'dev_abc123',
        now: now,
        publicKey: publicKey,
      );

      expect(result.valid, isFalse);
      expect(result.reason, 'bad_signature');
    });

    test('wrong device', () {
      final (privateKey, publicKey) = _keypair();
      final now = _utcnow();
      final token = issueToken(
        privateKey,
        deviceId: 'dev_abc123',
        customer: 'cust_001',
        days: 30,
        issuedAt: now,
      );

      final result = verifyToken(
        token,
        deviceId: 'other_device',
        now: now,
        publicKey: publicKey,
      );

      expect(result.valid, isFalse);
      expect(result.reason, 'device_mismatch');
    });

    test('deviceId hashes injectable android id', () {
      final id = deviceIdFromAndroidId('hardware-uuid-xyz');
      expect(id.length, 16);
      expect(id, deviceIdFromAndroidId('hardware-uuid-xyz'));
      expect(id, isNot(deviceIdFromAndroidId('other')));
    });

    test('LicenseGate.check reports missing token', () async {
      final checked = await LicenseGate.check(
        tokenOverride: '',
        deviceIdOverride: 'dev_abc123',
        now: _utcnow(),
      );

      expect(checked.result.valid, isFalse);
      expect(checked.result.reason, 'missing_token');
      expect(checked.badge, 'red');
      expect(checked.deviceId, 'dev_abc123');
    });

    test('badge colors match PC thresholds', () {
      expect(licenseBadge(allowed: false, daysLeft: 10), 'red');
      expect(licenseBadge(allowed: true, daysLeft: 3), 'yellow');
      expect(licenseBadge(allowed: true, daysLeft: 5), 'yellow');
      expect(licenseBadge(allowed: true, daysLeft: 6), 'green');
    });
  });

  group('canonical payload encoding', () {
    test('matches sorted compact JSON used by PC signer', () {
      final payload = <String, Object>{
        'customer': 'cust_001',
        'device_id': 'dev_abc123',
        'expires_at': '2026-08-21T12:00:00+00:00',
        'issued_at': '2026-07-22T12:00:00+00:00',
      };
      final encoded = encodeCanonicalJson(payload);
      expect(
        utf8.decode(encoded),
        '{"customer":"cust_001","device_id":"dev_abc123",'
        '"expires_at":"2026-08-21T12:00:00+00:00",'
        '"issued_at":"2026-07-22T12:00:00+00:00"}',
      );
      // Ensure signing input is raw bytes, not String.
      expect(encoded, isA<Uint8List>());
    });
  });
}
