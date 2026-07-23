/// Build a small JPEG thumbnail for relay slip_event (outside signed payload).
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:image/image.dart' as img;

/// Max width for relay thumbnails (keeps base64 well under relay 200KB cap).
const int kSlipThumbnailMaxWidth = 480;

/// JPEG quality 1–100.
const int kSlipThumbnailJpegQuality = 55;

/// Soft cap on base64 length; returns null if still too large after encode.
const int kSlipThumbnailMaxBase64Chars = 100000;

/// Returns base64 JPEG thumbnail, or null when the file cannot be read/encoded.
String? makeThumbnailJpegBase64(String imagePath) {
  try {
    final file = File(imagePath);
    if (!file.existsSync()) {
      return null;
    }
    final bytes = file.readAsBytesSync();
    final decoded = img.decodeImage(bytes);
    if (decoded == null) {
      return null;
    }

    img.Image sized = decoded;
    if (decoded.width > kSlipThumbnailMaxWidth) {
      final h = math.max(
        1,
        (decoded.height * kSlipThumbnailMaxWidth / decoded.width).round(),
      );
      sized = img.copyResize(
        decoded,
        width: kSlipThumbnailMaxWidth,
        height: h,
        interpolation: img.Interpolation.average,
      );
    }

    final jpeg = img.encodeJpg(sized, quality: kSlipThumbnailJpegQuality);
    final b64 = base64Encode(Uint8List.fromList(jpeg));
    if (b64.length > kSlipThumbnailMaxBase64Chars) {
      return null;
    }
    return b64;
  } catch (_) {
    return null;
  }
}
