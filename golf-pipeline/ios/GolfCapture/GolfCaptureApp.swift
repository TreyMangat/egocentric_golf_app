import SwiftUI

@main
struct GolfCaptureApp: App {
    @StateObject private var session = CaptureSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
                .preferredColorScheme(.dark)
                .statusBar(hidden: true)
        }
    }
}
