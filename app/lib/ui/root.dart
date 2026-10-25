import 'package:flutter/material.dart';

import '../app_model.dart';
import 'camera_screen.dart';
import 'dex_screen.dart';
import 'settings_screen.dart';

class RootScreen extends StatefulWidget {
  const RootScreen({super.key, required this.model});
  final AppModel model;

  @override
  State<RootScreen> createState() => _RootScreenState();
}

class _RootScreenState extends State<RootScreen> with WidgetsBindingObserver {
  int _tab = 0;

  AppModel get m => widget.model;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    m.addListener(_onModel);
  }

  bool _started = false;
  void _onModel() {
    // 로딩이 끝나면 카메라 탭에서 자동 시작. 종 모델이 없으면(아직 학습 전) 도감 탭으로 연다
    if (_started || !m.loaded || m.loadError != null) return;
    _started = true;
    if (!m.hasSpeciesModel) {
      if (_tab == 0) setState(() => _tab = 1);
    } else if (_tab == 0) {
      m.startCamera();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed && _tab == 0) m.startCamera();
    if (s == AppLifecycleState.paused || s == AppLifecycleState.inactive) m.stopCamera();
  }

  void _select(int i) {
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() => _tab = i);
    if (i == 0) {
      m.startCamera();
    } else {
      m.stopCamera();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    m.removeListener(_onModel);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _tab, children: [
        CameraScreen(model: m),
        DexScreen(model: m),
        SettingsScreen(model: m),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: _select,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.camera_alt_outlined), selectedIcon: Icon(Icons.camera_alt), label: '카메라'),
          NavigationDestination(icon: Icon(Icons.menu_book_outlined), selectedIcon: Icon(Icons.menu_book), label: '도감'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: '설정'),
        ],
      ),
    );
  }
}
