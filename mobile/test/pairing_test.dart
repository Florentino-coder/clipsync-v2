import 'package:clipsync_app/clip_service.dart';
import 'package:clipsync_app/slip/slip_bootstrap.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  test('new pairing URL parses id and secret', () {
    final result = parsePairingCode(
      'clipsync://pair?id=123456789&secret=abcdef0123456789abcdef0123456789',
    );

    expect(result, isNotNull);
    expect(result!.id, '123456789');
    expect(result.secret, 'abcdef0123456789abcdef0123456789');
  });

  test('old pairing URL parses id only without crash', () {
    final result = parsePairingCode('clipsync://pair?id=123456789');

    expect(result, isNotNull);
    expect(result!.id, '123456789');
    expect(result.secret, isNull);
  });

  test('plain nine-digit id still parses for clipboard sync', () {
    final result = parsePairingCode('123-456-789');

    expect(result, isNotNull);
    expect(result!.id, '123456789');
    expect(result.secret, isNull);
  });

  test('shared secret persists in SharedPreferences', () async {
    SharedPreferences.setMockInitialValues({});
    await saveSharedSecret('abcdef0123456789abcdef0123456789');
    expect(await loadSharedSecret(), 'abcdef0123456789abcdef0123456789');
    await saveSharedSecret(null);
    expect(await loadSharedSecret(), isNull);
  });
}
