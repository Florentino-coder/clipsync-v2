import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'license_service.dart';

const String kLicenseTokenPrefsKey = 'license_token';

/// Result of [LicenseGate.check] — offline verify without UI.
class LicenseCheckResult {
  const LicenseCheckResult({
    required this.result,
    required this.deviceId,
    required this.badge,
    required this.token,
  });

  final VerifyResult result;
  final String deviceId;
  final String badge;
  final String token;
}

/// Offline license gate shown before the main app shell.
class LicenseGate extends StatefulWidget {
  const LicenseGate({
    super.key,
    required this.child,
    this.tokenOverride,
    this.deviceIdOverride,
    this.now,
  });

  final Widget child;

  /// Injected token for tests; when null, loads from SharedPreferences.
  final String? tokenOverride;

  /// Injected device id for tests.
  final String? deviceIdOverride;

  final DateTime? now;

  /// Verify stored or overridden token offline (no widget build).
  static Future<LicenseCheckResult> check({
    String? tokenOverride,
    String? deviceIdOverride,
    DateTime? now,
    SharedPreferences? prefs,
  }) async {
    final String stored;
    if (tokenOverride != null) {
      stored = tokenOverride;
    } else {
      final preferences = prefs ?? await SharedPreferences.getInstance();
      stored = preferences.getString(kLicenseTokenPrefsKey) ?? '';
    }
    final deviceId = deviceIdOverride ?? await getDeviceId();
    final result = stored.isEmpty
        ? const VerifyResult(valid: false, reason: 'missing_token')
        : verifyToken(
            stored,
            deviceId: deviceId,
            now: now,
          );
    return LicenseCheckResult(
      result: result,
      deviceId: deviceId,
      badge: licenseBadge(allowed: result.valid, daysLeft: result.daysLeft),
      token: stored,
    );
  }

  @override
  State<LicenseGate> createState() => _LicenseGateState();
}

class _LicenseGateState extends State<LicenseGate> {
  bool _loading = true;
  VerifyResult? _result;
  String _deviceId = '';
  String _badge = 'red';
  final _tokenCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    unawaited(_evaluate());
  }

  @override
  void dispose() {
    _tokenCtrl.dispose();
    super.dispose();
  }

  Future<void> _evaluate({String? token}) async {
    setState(() => _loading = true);
    if (token != null) {
      final prefs = await SharedPreferences.getInstance();
      final probe = await LicenseGate.check(
        tokenOverride: token,
        deviceIdOverride: widget.deviceIdOverride,
        now: widget.now,
        prefs: prefs,
      );
      if (probe.result.valid) {
        await prefs.setString(kLicenseTokenPrefsKey, token);
      }
    }
    final checked = await LicenseGate.check(
      tokenOverride: token ?? widget.tokenOverride,
      deviceIdOverride: widget.deviceIdOverride,
      now: widget.now,
    );
    if (!mounted) return;
    setState(() {
      _deviceId = checked.deviceId;
      _result = checked.result;
      _badge = checked.badge;
      _loading = false;
      if (checked.token.isNotEmpty) {
        _tokenCtrl.text = checked.token;
      }
    });
  }

  Future<void> _submitToken() async {
    final raw = _tokenCtrl.text.trim();
    if (raw.isEmpty) return;
    await _evaluate(token: raw);
  }

  Color _badgeColor(ColorScheme cs) {
    switch (_badge) {
      case 'green':
        return Colors.green.shade600;
      case 'yellow':
        return Colors.amber.shade800;
      default:
        return cs.error;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }

    final result = _result!;
    if (result.valid) {
      return LicenseStatusScope(
        result: result,
        badge: _badge,
        deviceId: _deviceId,
        child: widget.child,
      );
    }

    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 24),
              Icon(Icons.lock_outline, size: 48, color: cs.error),
              const SizedBox(height: 16),
              Text(
                'License required',
                style: Theme.of(context).textTheme.headlineSmall,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                result.reason == 'expired'
                    ? 'หมดอายุ — ล็อก'
                    : result.reason == 'device_mismatch'
                        ? 'Token is bound to another device'
                        : 'Paste a valid license token to continue',
                textAlign: TextAlign.center,
                style: TextStyle(color: cs.onSurfaceVariant),
              ),
              const SizedBox(height: 24),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                decoration: BoxDecoration(
                  color: _badgeColor(cs).withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(18),
                ),
                child: Text(
                  licenseBadgeLabel(result),
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: _badgeColor(cs),
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _tokenCtrl,
                minLines: 3,
                maxLines: 5,
                decoration: const InputDecoration(
                  labelText: 'License token',
                  border: OutlineInputBorder(),
                  alignLabelWithHint: true,
                ),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _submitToken,
                child: const Text('Unlock'),
              ),
              const SizedBox(height: 16),
              SelectableText(
                'Device ID: $_deviceId',
                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
              ),
              TextButton.icon(
                onPressed: () async {
                  await Clipboard.setData(ClipboardData(text: _deviceId));
                  if (!context.mounted) return;
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Device ID copied')),
                  );
                },
                icon: const Icon(Icons.copy, size: 16),
                label: const Text('Copy device ID'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Provides verified license status to descendant widgets (badge / settings).
class LicenseStatusScope extends InheritedWidget {
  const LicenseStatusScope({
    super.key,
    required this.result,
    required this.badge,
    required this.deviceId,
    required super.child,
  });

  final VerifyResult result;
  final String badge;
  final String deviceId;

  static LicenseStatusScope? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<LicenseStatusScope>();
  }

  @override
  bool updateShouldNotify(LicenseStatusScope oldWidget) {
    return result != oldWidget.result ||
        badge != oldWidget.badge ||
        deviceId != oldWidget.deviceId;
  }
}
