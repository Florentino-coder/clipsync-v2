import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:image/image.dart' as img;
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as shelf_io;
import 'package:shelf_web_socket/shelf_web_socket.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'slip_event.dart';
import 'slip_store.dart';

/// Local HTTP/WebSocket server for USB-tethered slip fetch from PC.
///
/// `/ping` is intentionally **unauthenticated** so PC USB discovery can probe
/// for ClipSync without a shared secret. All other HTTP routes and WebSocket
/// connections require HMAC auth via [authToken] / `X-Auth` header.
class LocalSlipServer {
  LocalSlipServer(this.store, this.sharedSecret);

  final SlipStore store;
  final String sharedSecret;

  static const maxImagesPerRequest = 50;
  static const defaultPort = 8790;

  HttpServer? _server;
  final List<WebSocketChannel> _wsClients = [];

  /// Connected PC WebSocket clients (after auth).
  int get wsClientCount => _wsClients.length;

  /// Bound port (useful when [start] is called with port 0).
  int get port => _server?.port ?? defaultPort;

  /// HMAC-SHA256 hex digest used for auth (`X-Auth` header / WS auth message).
  String get authToken => Hmac(sha256, utf8.encode(sharedSecret))
      .convert(utf8.encode('clipsync-slip'))
      .toString();

  bool _authed(Request req) {
    final token = req.headers['x-auth'] ?? '';
    return token == authToken;
  }

  Future<void> start({int port = defaultPort}) async {
    final handler = Cascade()
        .add(_webSocketHandler())
        .add(_httpHandler)
        .handler;

    _server = await shelf_io.serve(
      handler,
      InternetAddress.anyIPv4,
      port,
    );
  }

  Future<void> stop() async {
    for (final client in List<WebSocketChannel>.from(_wsClients)) {
      await client.sink.close();
    }
    _wsClients.clear();
    await _server?.close(force: true);
    _server = null;
  }

  Handler _webSocketHandler() {
    return webSocketHandler((WebSocketChannel webSocket, _) {
      var authed = false;

      webSocket.stream.listen(
        (message) {
          if (!authed) {
            try {
              final decoded = jsonDecode(message as String);
              if (decoded is Map &&
                  decoded['type'] == 'auth' &&
                  decoded['token'] == authToken) {
                authed = true;
                _wsClients.add(webSocket);
                webSocket.sink.add(jsonEncode({'type': 'auth_ok'}));
                return;
              }
            } catch (_) {
              // Fall through to auth failure.
            }

            webSocket.sink.add(jsonEncode({'type': 'auth_failed'}));
            webSocket.sink.close();
            return;
          }

          // Stub: authenticated clients may send acks or other messages later.
        },
        onDone: () {
          _wsClients.remove(webSocket);
        },
        onError: (_) {
          _wsClients.remove(webSocket);
        },
        cancelOnError: true,
      );
    });
  }

  Future<Response> _httpHandler(Request req) async {
    final path = req.requestedUri.path;

    // Health check for USB discovery — no auth required.
    if (path == '/ping') {
      return Response.ok(
        jsonEncode({'app': 'clipsync', 'role': 'phone'}),
        headers: {'content-type': 'application/json'},
      );
    }

    if (!_authed(req)) {
      return Response(401);
    }

    if (path == '/slips') {
      final fromParam = req.requestedUri.queryParameters['from'];
      final toParam = req.requestedUri.queryParameters['to'];
      if (fromParam == null || toParam == null) {
        return Response(400, body: 'missing from/to');
      }

      final from = DateTime.parse(fromParam);
      final to = DateTime.parse(toParam);
      final items = (await store.byDateRange(from, to))
          .take(maxImagesPerRequest);

      final out = <Map<String, dynamic>>[];
      for (final slip in items) {
        final bytes = await File(slip.imagePath).readAsBytes();
        final decoded = img.decodeImage(bytes);
        if (decoded == null) {
          continue;
        }
        final jpeg = img.encodeJpg(decoded, quality: 75);
        out.add({
          'event_id': slip.eventId,
          'ref_number': slip.refNumber,
          'image_base64': base64Encode(jpeg),
        });
      }

      return Response.ok(
        jsonEncode(out),
        headers: {'content-type': 'application/json'},
      );
    }

    return Response.notFound('');
  }

  /// Push a slip event to all authenticated WebSocket clients.
  void pushSlipEvent(SlipEvent event) {
    final message = jsonEncode({
      'type': 'slip_event',
      'payload': event.toJson(),
    });
    for (final client in List<WebSocketChannel>.from(_wsClients)) {
      client.sink.add(message);
    }
  }
}
