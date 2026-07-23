import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:clipsync_app/slip/slip_thumbnail.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;

void main() {
  late Directory tempDir;

  setUp(() async {
    tempDir = await Directory.systemTemp.createTemp('slip_thumb_');
  });

  tearDown(() async {
    if (await tempDir.exists()) {
      await tempDir.delete(recursive: true);
    }
  });

  test('makeThumbnailJpegBase64 returns small jpeg under size cap', () async {
    final big = img.Image(width: 1200, height: 1600);
    img.fill(big, color: img.ColorRgb8(40, 80, 200));
    final path = '${tempDir.path}/slip.png';
    await File(path).writeAsBytes(img.encodePng(big));

    final b64 = makeThumbnailJpegBase64(path);
    expect(b64, isNotNull);
    expect(b64!.isNotEmpty, isTrue);
    expect(b64.length, lessThan(120000));

    final bytes = base64Decode(b64);
    final decoded = img.decodeJpg(Uint8List.fromList(bytes));
    expect(decoded, isNotNull);
    expect(decoded!.width, lessThanOrEqualTo(kSlipThumbnailMaxWidth));
  });

  test('makeThumbnailJpegBase64 returns null for missing file', () {
    expect(makeThumbnailJpegBase64('${tempDir.path}/missing.jpg'), isNull);
  });
}
