import SwiftUI

struct ClubPicker: View {
    @Binding var selected: Club
    @State private var showFull = false

    private let common: [Club] = [.driver, .sevenI, .pw, .sw]

    var body: some View {
        HStack(spacing: 8) {
            ForEach(common) { club in
                Button { selected = club } label: {
                    Text(club.displayName)
                        .font(.system(.caption, design: .monospaced).weight(.bold))
                        .foregroundColor(selected == club ? .black : .white)
                        .frame(width: 56, height: 32)
                        .background(
                            RoundedRectangle(cornerRadius: 4)
                                .fill(selected == club ? Color(hex: 0xd4ff5a) : Color.white.opacity(0.08))
                        )
                }
            }
            Button { showFull = true } label: {
                Text("•••")
                    .font(.system(.caption, design: .monospaced).weight(.bold))
                    .foregroundColor(.white)
                    .frame(width: 40, height: 32)
                    .background(
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color.white.opacity(0.08))
                    )
            }
        }
        .sheet(isPresented: $showFull) {
            FullClubGrid(selected: $selected)
                .presentationDetents([.medium])
        }
    }
}

private struct FullClubGrid: View {
    @Binding var selected: Club
    @Environment(\.dismiss) private var dismiss
    private let cols = [GridItem(.adaptive(minimum: 64), spacing: 8)]

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 16) {
                Text("Select club")
                    .font(.system(.subheadline, design: .monospaced))
                    .foregroundColor(.white.opacity(0.6))
                    .padding(.top)

                LazyVGrid(columns: cols, spacing: 8) {
                    ForEach(Club.allCases) { club in
                        Button {
                            selected = club
                            dismiss()
                        } label: {
                            Text(club.displayName)
                                .font(.system(.body, design: .monospaced).weight(.bold))
                                .foregroundColor(selected == club ? .black : .white)
                                .frame(maxWidth: .infinity, minHeight: 44)
                                .background(
                                    RoundedRectangle(cornerRadius: 4)
                                        .fill(selected == club ? Color(hex: 0xd4ff5a) : Color.white.opacity(0.08))
                                )
                        }
                    }
                }
                .padding()
                Spacer()
            }
        }
    }
}

extension Color {
    init(hex: UInt32) {
        let r = Double((hex >> 16) & 0xff) / 255
        let g = Double((hex >> 8) & 0xff) / 255
        let b = Double(hex & 0xff) / 255
        self.init(red: r, green: g, blue: b)
    }
}
