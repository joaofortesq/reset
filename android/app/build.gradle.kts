plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "br.manutencao.reset"
    compileSdk = 34

    defaultConfig {
        applicationId = "br.manutencao.reset"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    signingConfigs {
        // Chave fixa guardada no repositório: permite atualizar o app sem desinstalar.
        // Uso interno; para publicar na Play Store, trocar por uma chave própria e secreta.
        create("interna") {
            storeFile = file("chave-interna.jks")
            storePassword = "manutencao"
            keyAlias = "interna"
            keyPassword = "manutencao"
        }
    }

    buildTypes {
        getByName("debug") {
            signingConfig = signingConfigs.getByName("interna")
        }
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("interna")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
