import 'dart:convert';
import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

import 'clip_service.dart';

const kUpdateManifestUrl =
    'https://github.com/Florentino-coder/clipsync/releases/download/android-latest/version.json';
const _updateCheckInterval = Duration(hours: 24);
const _maxManifestBytes = 64 * 1024;

class UpdateInfo {
  const UpdateInfo({
    required this.version,
    required this.url,
    required this.notes,
  });

  final String version;
  final String url;
  final String notes;
}

Future<UpdateInfo?> checkAndroidUpdate({bool force = false}) async {
  final prefs = await SharedPreferences.getInstance();
  final now = DateTime.now().millisecondsSinceEpoch;
  final lastChecked = prefs.getInt('update_last_checked') ?? 0;
  final elapsed = Duration(milliseconds: now - lastChecked);

  if (!force && elapsed < _updateCheckInterval) {
    return null;
  }

  final client = HttpClient()..connectionTimeout = const Duration(seconds: 8);
  try {
    final request = await client.getUrl(Uri.parse(kUpdateManifestUrl));
    request.headers.set(HttpHeaders.userAgentHeader, 'ClipSync/$kAppVersion');
    final response = await request.close().timeout(const Duration(seconds: 10));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw const HttpException('update manifest unavailable');
    }

    final bytes = <int>[];
    await for (final chunk in response) {
      bytes.addAll(chunk);
      if (bytes.length > _maxManifestBytes) {
        throw const FormatException('update manifest too large');
      }
    }
    final body = utf8.decode(bytes);

    await prefs.setInt('update_last_checked', now);
    final data = jsonDecode(body);
    if (data is! Map) return null;
    final android = data['android'];
    if (android is! Map) return null;

    final latest = '${android['version'] ?? ''}'.trim();
    final url = '${android['url'] ?? ''}'.trim();
    if (latest.isEmpty ||
        url.isEmpty ||
        !_isNewerVersion(latest, kAppVersion)) {
      return null;
    }

    return UpdateInfo(
      version: latest,
      url: url,
      notes: '${android['notes'] ?? ''}'.trim(),
    );
  } finally {
    client.close(force: true);
  }
}

Future<bool> openUpdateUrl(String url) async {
  final uri = Uri.tryParse(url);
  if (uri == null) return false;
  return launchUrl(uri, mode: LaunchMode.externalApplication);
}

bool _isNewerVersion(String latest, String current) {
  final a = _parseVersion(latest);
  final b = _parseVersion(current);
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return a[i] > b[i];
  }
  return false;
}

List<int> _parseVersion(String value) {
  final parts = value.split('+');
  final base = parts.first.split('.');
  final parsed = <int>[];

  for (final part in base.take(3)) {
    parsed.add(int.tryParse(part.replaceAll(RegExp(r'[^0-9]'), '')) ?? 0);
  }
  while (parsed.length < 3) {
    parsed.add(0);
  }

  final build = parts.length > 1
      ? int.tryParse(parts[1].replaceAll(RegExp(r'[^0-9]'), '')) ?? 0
      : 0;
  parsed.add(build);
  return parsed;
}
